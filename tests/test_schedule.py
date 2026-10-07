from dataclasses import replace
from datetime import datetime, timezone
import pytest
from support import config
from dochunt.schedule import due


def cfg(**changes):
    c=config(); c.schedule=replace(c.schedule,enabled=True,**changes); return c


def at(value): return datetime.fromisoformat(value).astimezone(timezone.utc)


@pytest.mark.parametrize('when,expected,reason',[
    ('2026-10-02T12:59:00+00:00',False,'before'),
    ('2026-10-02T13:17:00+00:00',True,'due'),
    ('2026-10-02T16:59:00+00:00',True,'due'),
    ('2026-10-02T17:00:00+00:00',False,'expired'),
    ('2026-10-03T13:17:00+00:00',False,'weekday'),
    ('2026-03-09T13:17:00+00:00',True,'due'),
    ('2026-11-02T14:17:00+00:00',True,'due'),
])
def test_local_schedule(when,expected,reason):
    d=due(cfg(timezone='America/Chicago'),at(when),{})
    assert d.due is expected and reason in d.reason


def test_disabled_completed_unknown():
    now=at('2026-10-02T13:17:00+00:00'); c=cfg(timezone='America/Chicago')
    assert not due(config(),now,{}).due
    key=f'{c.identity.id}:2026-10-02'
    assert not due(c,now,{'completed':{key:'x'}}).due
    state={'attempts':{'x':{'status':'unknown','prepared':{'preview_test':False,'provider':'smtp','issue':{'issue_key':key}}}}}
    assert not due(c,now,state).due
    assert not due(c,now,state,manual=True).due
    assert due(config(),at('2026-10-02T22:00:00+00:00'),{},manual=True).issue_key.endswith('2026-10-02')


def test_naive_clock_rejected():
    with pytest.raises(ValueError): due(cfg(),datetime(2026,10,2),{})


@pytest.mark.parametrize('when,preferred,expected',[
    ('2026-03-08T07:59:00+00:00','02:30',False),
    ('2026-03-08T08:30:00+00:00','02:30',True),
    ('2026-11-01T06:30:00+00:00','01:30',True),
    ('2026-11-01T07:30:00+00:00','01:30',True),
    ('2026-11-01T10:30:00+00:00','01:30',False),
])
def test_dst_gap_and_repeated_hour(when,preferred,expected):
    assert due(cfg(timezone='America/Chicago',weekdays=[6],preferred_time=preferred),at(when),{}).due is expected
