from urllib.parse import quote
from http.cookies import SimpleCookie,CookieError
import httpx
from pydantic import ValidationError
from app.external.correspondencia.errors import CorrespondenciaError
from app.external.correspondencia.schemas import Actor, Employee


class CorrespondenciaClient:
    def __init__(self, config, transport=None):
        self.config, self.transport = config, transport

    def auth_request(self,operation,*,payload=None,refresh_token=None,cookie_name='refresh_token'):
        """Relay only the existing session protocol. No technical token or storage."""
        if operation not in {'login','refresh','logout'} or not self.config.url:
            raise CorrespondenciaError('correspondencia_not_configured')
        try:
            with httpx.Client(base_url=self.config.url.rstrip('/')+'/',timeout=self.config.timeout,
                    transport=self.transport,follow_redirects=False) as client:
                if refresh_token:
                    client.cookies.set(cookie_name,refresh_token)
                response=client.post('auth/'+operation,json=payload)
        except httpx.TimeoutException:
            raise CorrespondenciaError('correspondencia_timeout',504) from None
        except httpx.HTTPError:
            raise CorrespondenciaError('correspondencia_unavailable') from None
        if response.status_code>=300:
            raise CorrespondenciaError('correspondencia_rejected' if response.status_code in {401,403} else
                'correspondencia_unavailable',response.status_code if response.status_code in {401,403} else 503)
        if operation=='logout': return {},None
        try:
            data=response.json()
            if not isinstance(data,dict) or not isinstance(data.get('access_token'),str) or not data['access_token']:
                raise ValueError()
            for header in response.headers.get_list('set-cookie'):
                cookies=SimpleCookie();cookies.load(header)
                if cookie_name in cookies:
                    morsel=cookies[cookie_name]
                    if not morsel.value: raise ValueError()
                    return data,{'value':morsel.value,'max_age':int(morsel['max-age']) if morsel['max-age'] else None,
                                 'expires':morsel['expires'] or None}
            raise ValueError()
        except (ValueError,TypeError,KeyError,CookieError):
            raise CorrespondenciaError('correspondencia_invalid_session_response') from None

    def request(self, path, params=None, *, bearer=None):
        token = self.config.token if bearer is None else bearer
        if not self.config.url or not token:
            raise CorrespondenciaError('correspondencia_not_configured')
        try:
            with httpx.Client(base_url=self.config.url.rstrip('/')+'/', timeout=self.config.timeout,
                    transport=self.transport, follow_redirects=False) as client:
                result = client.get(path.lstrip('/'), params=params,
                    headers={'Authorization': 'Bearer '+token, 'Accept': 'application/json'})
        except httpx.TimeoutException:
            raise CorrespondenciaError('correspondencia_timeout', 504) from None
        except httpx.HTTPError:
            raise CorrespondenciaError('correspondencia_unavailable') from None
        if result.status_code >= 300:
            # Existing Correspondencia auth contract: a verified subject that is
            # absent/inactive returns this exact legacy detail with HTTP 401.
            # Keep invalid JWTs 401; normalize only this audited identity failure.
            if path=='auth/me' and result.status_code==401:
                try: unavailable=result.json()=={'detail':'Usuario no disponible'}
                except ValueError: unavailable=False
                if unavailable: raise CorrespondenciaError('inactive_actor',403)
            status = result.status_code if result.status_code in {401,403,404} else 503
            raise CorrespondenciaError('correspondencia_rejected' if status in {401,403} else
                'employee_not_found' if status == 404 else 'correspondencia_unavailable', status)
        try:
            return result.json()
        except ValueError:
            raise CorrespondenciaError('correspondencia_invalid_response') from None

    def get_authenticated_user(self, bearer):
        try:
            return Actor.model_validate(self.request('auth/me', bearer=bearer))
        except ValidationError:
            raise CorrespondenciaError('correspondencia_invalid_response') from None

    def get_employee(self, employee_id):
        try:
            value = Employee.model_validate(self.request('admin/employees/'+quote(str(employee_id), safe='')))
            if str(value.id) != str(employee_id):
                raise CorrespondenciaError('correspondencia_identity_mismatch')
            return value
        except ValidationError:
            raise CorrespondenciaError('correspondencia_invalid_response') from None

    def list_employees(self, **filters):
        payload = self.request('admin/employees', filters)
        try:
            page = int(payload['page']); total_pages = int(payload['total_pages'])
            employees = [Employee.model_validate(row) for row in payload['items']]
            if page < 1 or total_pages < 0:
                raise ValueError()
            return employees, page, total_pages
        except (ValueError, KeyError, TypeError):
            raise CorrespondenciaError('correspondencia_invalid_response') from None

    def find_employee_by_document(self, document):
        matches, page = [], 1
        while page <= 1000:
            rows, current, pages = self.list_employees(page=page, page_size=100, search=document, include_deleted=True)
            if current != page:
                raise CorrespondenciaError('correspondencia_pagination_invalid')
            matches.extend(row for row in rows if row.document_number == document)
            if page >= pages:
                if len(matches) > 1:
                    raise CorrespondenciaError('employee_document_ambiguous', 409)
                return matches[0] if matches else None
            page += 1
        raise CorrespondenciaError('correspondencia_pagination_limit')

    def get_area(self, area_id):
        return self.request('admin/organizational-units/'+quote(str(area_id), safe=''))

    def get_position(self, position_id):
        return self.request('admin/positions/'+quote(str(position_id), safe=''))

    def get_user(self, user_id):
        return self.request('admin/users/'+quote(str(user_id), safe=''))

    def get_roles(self):
        return self.request('admin/roles')
