"""Paginación estable por snapshot: todas las páginas provienen del mismo conjunto.

La API biométrica lee el dispositivo una sola vez por consulta y devuelve el
`snapshot_id` de ese conjunto. El cliente debe reutilizarlo en cada página
siguiente para no volver a consultar el reloj y para que el total no cambie.
"""
import httpx
import pytest
from app.biometric.api_client.config import ApiConfig
from app.biometric.api_client.client import BiometricApiClient
from app.biometric.api_client.errors import BiometricApiError

SNAPSHOT = 'snapshot-abc-123'


def client(handler):
    return BiometricApiClient(ApiConfig('https://biometric.example.test', 'fixture-token', 5, 'test-clock'),
                              httpx.MockTransport(handler))


def page(records, total, offset=0, limit=2, snapshot_id=SNAPSHOT):
    value = {'device_id': 'test-clock', 'records': records, 'total': total, 'count': len(records),
             'offset': offset, 'limit': limit}
    if snapshot_id is not None:
        value['snapshot_id'] = snapshot_id
    return value


def snapshot_handler(calls, total=5, size=2, snapshot_id=SNAPSHOT):
    """API simulada: entrega el conjunto por páginas y anota lo recibido."""
    def handler(request):
        offset = int(request.url.params['offset'])
        calls.append(dict(request.url.params))
        remaining = max(0, total - offset)
        count = min(size, remaining)
        return httpx.Response(200, json=page([{'record': offset + i} for i in range(count)],
                                             total, offset, size, snapshot_id))
    return handler


def test_la_primera_pagina_no_manda_snapshot():
    calls = []
    list(client(snapshot_handler(calls)).download_attendance(page_size=2))
    assert calls[0].get('snapshot_id') is None


def test_la_primera_respuesta_captura_el_snapshot():
    calls = []
    list(client(snapshot_handler(calls)).download_attendance(page_size=2))
    assert calls[0].get('snapshot_id') is None
    # La segunda llamada ya viaja con el snapshot de la primera respuesta.
    assert calls[1].get('snapshot_id') == SNAPSHOT


def test_la_segunda_pagina_manda_el_mismo_snapshot():
    calls = []
    list(client(snapshot_handler(calls)).download_attendance(page_size=2))
    assert calls[1]['snapshot_id'] == SNAPSHOT


def test_la_tercera_pagina_conserva_el_mismo_snapshot():
    calls = []
    list(client(snapshot_handler(calls, total=5, size=2)).download_attendance(page_size=2))
    assert len(calls) == 3
    assert calls[1]['snapshot_id'] == SNAPSHOT
    assert calls[2]['snapshot_id'] == SNAPSHOT
    assert len({call['snapshot_id'] for call in calls[1:]}) == 1


def test_el_total_permanece_estable():
    calls, totals = [], []
    def handler(request):
        offset = int(request.url.params['offset'])
        calls.append(dict(request.url.params))
        count = min(2, max(0, 5 - offset))
        value = page([{'record': offset + i} for i in range(count)], 5, offset, 2)
        totals.append(value['total'])
        return httpx.Response(200, json=value)
    registros = client(handler).download_attendance(page_size=2)
    assert len(registros) == 5
    assert [int(call['offset']) for call in calls] == [0, 2, 4]
    # El total informado por la API es el mismo en todas las páginas.
    assert totals == [5, 5, 5]


def test_los_filtros_originales_se_conservan_en_cada_pagina():
    calls = []
    list(client(snapshot_handler(calls)).download_attendance(
        page_size=2, from_date='2026-07-01', to_date='2026-07-02'))
    for call in calls:
        assert call['from'] == '2026-07-01'
        assert call['to'] == '2026-07-02'


def test_una_sola_pagina_sigue_funcionando():
    calls = []
    registros = client(snapshot_handler(calls, total=2, size=2)).download_attendance(page_size=2)
    assert len(registros) == 2
    assert len(calls) == 1
    assert calls[0].get('snapshot_id') is None


def test_un_snapshot_expirado_es_un_error_controlado():
    def handler(request):
        return httpx.Response(400, json={'detail': 'snapshot_expired: el conjunto ya no esta disponible.'})
    with pytest.raises(BiometricApiError) as error:
        client(handler).download_attendance(page_size=2)
    assert error.value.code == 'biometric_api_snapshot_expired'
    assert 'expiró' in str(error.value)


def test_sin_snapshot_en_la_api_se_conserva_el_comportamiento_anterior():
    """Una API antigua que no devuelve snapshot_id sigue paginando como antes."""
    calls = []
    def handler(request):
        offset = int(request.url.params['offset'])
        calls.append(dict(request.url.params))
        count = min(2, max(0, 3 - offset))
        return httpx.Response(200, json=page([{'record': offset + i} for i in range(count)], 3, offset, 2, None))
    registros = client(handler).download_attendance(page_size=2)
    assert len(registros) == 3
    assert all(call.get('snapshot_id') is None for call in calls)
