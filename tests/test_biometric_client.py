import httpx
import pytest
from app.biometric.api_client.config import ApiConfig
from app.biometric.api_client.client import BiometricApiClient
from app.biometric.api_client.errors import BiometricApiError


def client(handler):
    return BiometricApiClient(ApiConfig('https://biometric.example.test','fixture-token',5,'test-clock'),httpx.MockTransport(handler))


def page(records,total,offset=0,limit=2):
    return {'device_id':'test-clock','records':records,'total':total,'count':len(records),'offset':offset,'limit':limit}


def test_paginated_filtered_read_only():
    calls=[]
    def handler(request):
        assert request.method=='GET'
        assert request.headers['authorization']=='Bearer fixture-token'
        assert request.url.params['from']=='2026-07-01' and request.url.params['to']=='2026-07-02'
        offset=int(request.url.params['offset']); calls.append(offset)
        return httpx.Response(200,json=page([{'record':offset+i} for i in range(2 if offset==0 else 1)],3,offset))
    assert len(client(handler).download_attendance(page_size=2,from_date='2026-07-01',to_date='2026-07-02'))==3
    assert calls==[0,2]


@pytest.mark.parametrize('status,code',[(401,'biometric_api_unauthorized'),(403,'biometric_api_unauthorized'),
    (502,'biometric_device_unreachable'),(504,'biometric_api_timeout'),(500,'biometric_api_error')])
def test_errors(status,code):
    with pytest.raises(BiometricApiError) as error:
        client(lambda r:httpx.Response(status,json={'detail':'private secret'})).health()
    assert error.value.code==code and 'private' not in str(error.value)


def test_no_redirects_or_http():
    with pytest.raises(ValueError): ApiConfig('http://device','fixture-token')
    with pytest.raises(BiometricApiError):
        client(lambda r:httpx.Response(302,headers={'Location':'http://device'})).health()


def test_invalid_json_and_identity():
    with pytest.raises(BiometricApiError): client(lambda r:httpx.Response(200,text='not JSON')).health()
    with pytest.raises(BiometricApiError,match='otro dispositivo'):
        client(lambda r:httpx.Response(200,json={'device_id':'other'})).get_device()


def test_timeout():
    def handler(request): raise httpx.ReadTimeout('private',request=request)
    with pytest.raises(BiometricApiError) as error: client(handler).health()
    assert error.value.code=='biometric_api_timeout'


def test_changing_total_rejected():
    def handler(request):
        offset=int(request.url.params['offset'])
        return httpx.Response(200,json=page([{}]*2,3 if offset==0 else 4,offset))
    with pytest.raises(BiometricApiError) as error: client(handler).download_attendance(page_size=2)
    assert error.value.code=='biometric_api_snapshot_changed'


@pytest.mark.parametrize('bad',[{'offset':3},{'count':2},{'total':-1},{'limit':0}])
def test_invalid_page_rejected(bad):
    value={**page([{}],1),**bad}
    with pytest.raises(BiometricApiError): client(lambda r:httpx.Response(200,json=value)).download_attendance()
