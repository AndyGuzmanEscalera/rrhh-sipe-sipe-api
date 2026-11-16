from datetime import date,timedelta
import pytest
from app.biometric.sync_planner import SyncPlanner,SyncRange

TODAY=date(2026,7,30)
def day(n): return date(2026,7,n)
def run(start,end,coverage,force=False):
    return SyncPlanner().plan(day(start),day(end),coverage,TODAY,force=force)


def test_new_range():
    assert run(1,20,{})==(SyncRange(day(1),day(20)),)


def test_complete():
    assert run(1,20,{day(n):'COMPLETE' for n in range(1,21)})==()


def test_partial():
    assert run(1,30,{day(n):'COMPLETE' for n in range(1,21)})==(SyncRange(day(21),day(30)),)


def test_holes_failed_and_consecutive_ranges():
    coverage={day(n):'COMPLETE' for n in range(1,21)}
    coverage.update({day(11):'FAILED',day(12):'FAILED'})
    assert run(1,30,coverage)==(SyncRange(day(11),day(12)),SyncRange(day(21),day(30)))


@pytest.mark.parametrize('state',['OPEN','FAILED','COMPLETE'])
def test_today_never_closed(state):
    assert run(30,30,{day(30):state})==(SyncRange(day(30),day(30)),)


def test_force():
    assert run(15,15,{day(15):'COMPLETE'},True)==(SyncRange(day(15),day(15)),)


def test_tomorrow_closes_only_after_another_fetch():
    assert SyncPlanner().plan(day(30),day(30),{day(30):'OPEN'},day(31))==(SyncRange(day(30),day(30)),)


@pytest.mark.parametrize('start,end',[(day(20),day(1)),(day(1),day(31)),(date(2024,1,1),day(1))])
def test_invalid_ranges(start,end):
    with pytest.raises(ValueError): SyncPlanner().plan(start,end,{},TODAY)
