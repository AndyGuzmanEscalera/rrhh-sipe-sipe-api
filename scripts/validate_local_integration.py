"""Actual local HTTPS applications + a limited read of the deployed biometric API.

No changes to either external source tree. All DB writes target dedicated test DBs.
Tokens/personal evidence stay in ignored .local/. Never use with a real master DB.
"""
import os,sys,json,time,uuid,secrets,subprocess,logging,ipaddress
from pathlib import Path
from datetime import datetime,timedelta,timezone,date

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'.local/tools'))
LOCAL=ROOT/'.local/integration'
SOURCE=Path(r'C:\dev\correspondencia_sipe_sipe\backend')
CORR_DB='postgresql+psycopg://rrhh_test@127.0.0.1:55449/corr_integration_test_20261116'
RRHH_DB='postgresql+psycopg://rrhh_test@127.0.0.1:55449/rrhh_api_test_real_integration_20261116'
ACTOR='90000000-0000-4000-8000-000000000002'
EMPLOYEE='10000000-0000-4000-8000-000000000002'
AREA='30000000-0000-4000-8000-000000000002'
POSITION='40000000-0000-4000-8000-000000000002'
CONTRACT='20000000-0000-4000-8000-000000000002'
BIO_DAY='2026-11-11'


def run(command,cwd,env):
    result=subprocess.run(command,cwd=cwd,env=env,input=None,capture_output=True,text=True)
    if result.returncode: raise RuntimeError('local_setup_failed')


def certificates():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    import certifi
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'RRHH disposable integration loopback')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
        .not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
        .sign(key,hashes.SHA256()))
    (LOCAL/'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (LOCAL/'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (LOCAL/'ca-bundle.pem').write_bytes(Path(certifi.where()).read_bytes()+cert.public_bytes(serialization.Encoding.PEM))


def spawn(module,port,cwd,env):
    handle=(LOCAL/f'server-{port}.log').open('w',encoding='utf-8')
    command=[sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(port),'--no-access-log',
        '--ssl-keyfile',str(LOCAL/'server.key'),'--ssl-certfile',str(LOCAL/'server.pem')]
    process=subprocess.Popen(command,cwd=cwd,env=env,stdout=handle,stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return process,handle


def seed_master(env,password):
    seed=f'''
import uuid,os
from sqlalchemy import select
from app.main import app
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position
from app.modules.identity.user import User
from app.modules.identity.role import Role
from app.modules.identity.permission import Permission
from app.modules.identity.user_role import UserRole
from app.modules.identity.role_permission import RolePermission
with SessionLocal() as db:
    assert db.get_bind().url.database=='corr_integration_test_20261116'
    unit=OrganizationalUnit(id=uuid.UUID('{AREA}'),code='LOCAL-RRHH',name='Área de prueba local')
    position=Position(id=uuid.UUID('{POSITION}'),code='LOCAL-RRHH',name='Cargo de prueba local')
    db.add_all([unit,position]);db.flush()
    employee=Employee(id=uuid.UUID('{EMPLOYEE}'),first_name='Funcionario',last_name='Integración Local',document_number='RRHH-LOCAL-001',unit_id=unit.id,position_id=position.id)
    db.add(employee);db.flush()
    user=User(id=uuid.UUID('{ACTOR}'),username='rrhh_local_integration',password_hash=hash_password(os.environ['FIXTURE_PASSWORD']),employee_id=employee.id,is_active=True)
    db.add(user);db.flush()
    role=db.scalar(select(Role).where(Role.code=='ADMIN'))
    gestor=db.scalar(select(Role).where(Role.code=='MANAGER'))
    assert role is not None and role.is_active and gestor is not None and gestor.is_active, 'Existing institutional roles required'
    db.add_all([UserRole(user_id=user.id,role_id=role.id),UserRole(user_id=user.id,role_id=gestor.id)])
    for code in ['rrhh.read','rrhh.manage','master_data.employees.read','master_data.organizational_units.read','master_data.positions.read','master_data.users.read','master_data.roles.assign','master_data.users.manage']:
        permission=db.scalar(select(Permission).where(Permission.code==code))
        assert permission is not None, 'Existing permission missing'
        if db.get(RolePermission,(role.id,permission.id)) is None: db.add(RolePermission(role_id=role.id,permission_id=permission.id))
    db.commit()
'''
    result=subprocess.run([sys.executable,'-c',seed],cwd=SOURCE,env={**env,'FIXTURE_PASSWORD':password},capture_output=True,text=True)
    if result.returncode: raise RuntimeError('local_master_fixture_failed')


def main():
    import httpx
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url
    LOCAL.mkdir(parents=True,exist_ok=True)
    for value,prefix in [(CORR_DB,'corr_integration_test_'),(RRHH_DB,'rrhh_api_test_')]:
        parsed=make_url(value)
        assert parsed.host=='127.0.0.1' and parsed.database.startswith(prefix)
    if (LOCAL/'result.json').exists(): raise RuntimeError('already_executed_use_a_fresh_test_database')
    password=secrets.token_urlsafe(32)
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','DATABASE_URL':CORR_DB,'APP_ENV':'development',
        'JWT_ACCESS_SECRET':secrets.token_urlsafe(48),'COOKIE_SECURE':'true','COOKIE_PATH':'/auth','BIOMETRIC_API_TOKEN':''}
    run([sys.executable,'-m','alembic','upgrade','head'],SOURCE,env)
    seed_master(env,password)
    certificates()
    from app.core.config import get_settings
    config=dotenv_values(SOURCE/'.env')
    token=config.get('BIOMETRIC_API_TOKEN') or ''
    if not token: raise RuntimeError('biometric_token_not_configured')
    url=config.get('BIOMETRIC_API_URL') or 'https://biometric.sipesipe.gob.bo'
    assert url.rstrip('/')=='https://biometric.sipesipe.gob.bo'
    settings_env={**os.environ,'DATABASE_URL':RRHH_DB,'CORRESPONDENCIA_API_URL':'https://127.0.0.1:8310',
        'CORRESPONDENCIA_API_TOKEN':'','BIOMETRIC_API_URL':url,'BIOMETRIC_API_TOKEN':token,
        'BIOMETRIC_DEVICE_ID':config.get('BIOMETRIC_DEVICE_ID') or 'alcaldia-central','SCHEDULER_ENABLED':'false',
        'SSL_CERT_FILE':str(LOCAL/'ca-bundle.pem'),'SESSION_COOKIE_SECURE':'true','SESSION_COOKIE_PATH':'/auth',
        'PYTHONDONTWRITEBYTECODE':'1'}
    run([sys.executable,'-m','alembic','upgrade','head'],ROOT,settings_env)
    os.environ.update(settings_env);get_settings.cache_clear()
    logging.getLogger('httpx').setLevel(logging.WARNING)
    processes=[];result={'master_kind':'actual_application_with_disposable_fixture_data','biometric_day':BIO_DAY,'measurements':[]}
    def measured(name,action):
        start=time.perf_counter();value=action()
        status=getattr(value,'status_code',200)
        result['measurements'].append({'operation':name,'status':status,'milliseconds':round((time.perf_counter()-start)*1000,2)})
        if status>=400: raise RuntimeError(name+'_failed')
        return value
    try:
        processes.append(spawn('app.main:app',8310,SOURCE,{**env,'SSL_CERT_FILE':settings_env['SSL_CERT_FILE']}))
        processes.append(spawn('app.main:app',8311,ROOT,settings_env))
        with httpx.Client(verify=str(LOCAL/'ca-bundle.pem'),timeout=90) as browser:
            for port in (8310,8311):
                for _ in range(60):
                    try:
                        if browser.get(f'https://127.0.0.1:{port}/health').status_code==200: break
                    except httpx.HTTPError: time.sleep(.25)
                else: raise RuntimeError('local_https_startup_failed')
            base='https://127.0.0.1:8311'
            login=measured('facade_login',lambda:browser.post(base+'/auth/login',json={'username':'rrhh_local_integration','password':password}))
            bearer=login.json()['access_token'];headers={'Authorization':'Bearer '+bearer}
            refreshed=measured('facade_refresh',lambda:browser.post(base+'/auth/refresh'))
            bearer=refreshed.json()['access_token'];headers={'Authorization':'Bearer '+bearer}
            from app.external.correspondencia.client import CorrespondenciaClient
            from app.external.correspondencia.config import CorrespondenciaConfig
            master=CorrespondenciaClient(CorrespondenciaConfig('https://127.0.0.1:8310',bearer))
            measured('master_me',lambda:master.get_authenticated_user(bearer))
            measured('master_employees',lambda:master.list_employees(page=1,page_size=100))
            employee=measured('master_employee',lambda:master.get_employee(EMPLOYEE))
            assert measured('master_ci_lookup',lambda:master.find_employee_by_document('RRHH-LOCAL-001')).id==employee.id
            measured('master_area',lambda:master.get_area(AREA));measured('master_position',lambda:master.get_position(POSITION))
            measured('master_user',lambda:master.get_user(ACTOR));measured('master_roles',lambda:master.get_roles())
            from app.biometric.service import BiometricService
            bio=BiometricService()
            measured('biometric_health',bio.client.health)
            device=measured('biometric_device',bio.client.get_device)
            users=measured('biometric_users',bio.client.get_users)
            preview=measured('biometric_attendance_preview',lambda:bio.client.get_attendance(from_date=BIO_DAY,to_date=BIO_DAY,limit=1))
            result['device_id']=device.get('device_id');result['biometric_users_count']=len(users);result['remote_day_total']=preview['total']
            from app.biometric.worker import run_once
            from app.db.session import session_factory,get_engine
            from app.biometric.models import Sync,Record,Coverage
            from app.biometric.storage import sync_output
            from sqlalchemy import select,func
            def sync(force=False):
                response=measured('enqueue_force' if force else 'enqueue',lambda:browser.post(base+'/rrhh/biometric/sync',headers=headers,
                    json={'start_date':BIO_DAY,'end_date':BIO_DAY,'force':force}))
                job_id=uuid.UUID(response.json()['id'])
                measured('worker_force' if force else 'worker',lambda:run_once())
                with session_factory() as db:
                    job=db.get(Sync,job_id)
                    if job.status!='completed': raise RuntimeError('sync_not_completed_'+str(job.error_code))
                    output=sync_output(job)
                    return {k:output[k] for k in ['status','downloaded_records','new_records','duplicates_ignored','planned_ranges']}
            result['sync_first']=sync()
            result['sync_repeat']=sync()
            assert result['sync_repeat']['new_records']==0 and result['sync_repeat']['planned_ranges']==[]
            result['sync_force']=sync(True)
            with session_factory() as db:
                rows=list(db.scalars(select(Record).where(Record.date==date.fromisoformat(BIO_DAY))))
                assert rows and len(rows)==len({r.record_id for r in rows})
                bio_user=rows[0].biometric_user_id
                raw_before=[(r.record_id,r.timestamp,r.status_raw,r.punch_raw,r.uid_raw) for r in rows]
                result['stored_records']=len(rows)
                result['coverage']=db.scalar(select(Coverage.status).where(Coverage.date==date.fromisoformat(BIO_DAY)))
            from urllib.parse import quote
            measured('explicit_link',lambda:browser.put(base+f'/rrhh/biometric/devices/{quote(result["device_id"],safe="")}/links/{quote(bio_user,safe="")}',headers=headers,json={'funcionario_id':EMPLOYEE}))
            shift=json.loads((ROOT/'tests/fixtures/integration_shift.json').read_text())
            shift_response=measured('local_shift',lambda:browser.post(base+'/rrhh/work-shifts',headers=headers,json=shift))
            measured('local_parameters',lambda:browser.put(base+'/rrhh/attendance/parameters',headers=headers,json=json.loads((ROOT/'tests/fixtures/integration_parameters.json').read_text())))
            measured('assignment',lambda:browser.post(base+'/rrhh/employee-shift-assignments',headers=headers,json={
                'funcionario_id_external':EMPLOYEE,'shift_id':shift_response.json()['id'],'valid_from':'2026-11-01','valid_to':'2026-11-30'}))
            measured('calendar',lambda:browser.post(base+'/rrhh/calendar',headers=headers,json={'funcionario_id_external':EMPLOYEE,'date':BIO_DAY,'is_working':True}))
            measured('scope',lambda:browser.post(base+'/rrhh/attendance/policies',headers=headers,json={
                'contract_type_id_external':CONTRACT,'name':'PRUEBA LOCAL - ámbito técnico, no contrato institucional',
                'valid_from':'2026-11-01','valid_to':'2026-11-30','cutoff_day':31,'period_semantics_confirmed':True,
                'population_confirmed':True,'employee_ids_external':[EMPLOYEE],'confirmation_reference':'local integration fixture only'}))
            query={'contract_type_id':CONTRACT,'year':2026,'month':11,'start_date':BIO_DAY,'end_date':BIO_DAY}
            report=measured('report_complete',lambda:browser.get(base+f'/rrhh/attendance/employees/{EMPLOYEE}/report',headers=headers,params=query)).json()
            result['report_records']=report['summary']['total_records'];result['report_ready']=report['ready']
            assert report['employee']['full_name']=='Funcionario Integración Local'
            assert report['employee']['unit_name']=='Área de prueba local' and report['employee']['position_name']=='Cargo de prueba local'
            assert report['summary']['total_records']==sum(r.biometric_user_id==bio_user for r in rows)
            processes[-1][0].terminate();processes[-1][0].wait(timeout=15);processes[-1][1].close();processes.pop()
            offline={**settings_env,'BIOMETRIC_API_URL':'https://127.0.0.1:9','BIOMETRIC_API_TIMEOUT':'1'}
            processes.append(spawn('app.main:app',8311,ROOT,offline))
            for _ in range(60):
                try:
                    if browser.get(base+'/health').status_code==200: break
                except httpx.HTTPError: time.sleep(.25)
            unavailable=browser.get(base+'/rrhh/biometric/status',headers=headers)
            assert unavailable.status_code==503
            timings=[]
            for _ in range(3):
                started=time.perf_counter()
                down=measured('report_biometric_offline',lambda:browser.get(base+f'/rrhh/attendance/employees/{EMPLOYEE}/report',headers=headers,params=query))
                timings.append(round((time.perf_counter()-started)*1000,2))
                assert down.json()==report
            result['offline_status']=unavailable.status_code;result['report_offline_equal']=True;result['report_cached_ms']=timings
            with session_factory() as db:
                after=list(db.scalars(select(Record).where(Record.date==date.fromisoformat(BIO_DAY))))
                assert sorted(raw_before)==sorted((r.record_id,r.timestamp,r.status_raw,r.punch_raw,r.uid_raw) for r in after)
            result['raw_unchanged']=True
            measured('facade_logout',lambda:browser.post(base+'/auth/logout'))
        (LOCAL/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True,indent=2))
    finally:
        for process,handle in reversed(processes):
            if process.poll() is None: process.terminate();process.wait(timeout=15)
            handle.close()


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print('Integration incomplete: '+str(getattr(exc,'code',str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__)),file=sys.stderr)
        raise SystemExit(1)
