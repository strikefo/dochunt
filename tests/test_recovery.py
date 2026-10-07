from datetime import timedelta, datetime, timezone
import pytest
from test_delivery import prepared, Fake
from dochunt.delivery import send
from dochunt.state import StateStore


def test_old_pending_requires_manual_recovery(tmp_path):
    s=StateStore(tmp_path,git=False); p=prepared(); t=Fake()
    send(p,s,t)
    d=s.read(); d['attempts'][p.attempt_key]['status']='sending'; d['attempts'][p.attempt_key]['prepared']['prepared_at']=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(); s.save(d)
    with pytest.raises(RuntimeError): send(p,s,t)
    assert len(t.calls)==1


def test_configuration_cannot_mutate_pending_recipients(tmp_path):
    from dataclasses import asdict, replace
    p=prepared(); s=StateStore(tmp_path,git=False)
    s.save({'attempts':{p.attempt_key:{'prepared':asdict(p),'status':'pending'}}})
    t=Fake(); send(replace(p,recipient_snapshot=('changed@example.invalid',)),s,t)
    assert t.calls[0].recipient_snapshot==p.recipient_snapshot


def test_new_attempt_cannot_bypass_unknown(tmp_path):
    from dataclasses import asdict, replace
    p=prepared(); s=StateStore(tmp_path,git=False)
    s.save({'attempts':{p.attempt_key:{'prepared':asdict(p),'status':'unknown'}}})
    with pytest.raises(RuntimeError): send(replace(p,attempt_key='new-operator-id'),s,Fake())


def test_unknown_preview_does_not_block_normal_delivery(tmp_path):
    from dataclasses import asdict
    from support import config, sample_issue
    from dochunt.render import render_issue
    from dochunt.delivery import prepare
    from datetime import date
    c=config(); issue=sample_issue(c,date(2026,10,2)); rendered=render_issue(issue,c)
    preview=prepare(issue,rendered,c,attempt_id='test',recipient=c.email.recipients[0]); normal=prepare(issue,rendered,c)
    s=StateStore(tmp_path,git=False)
    s.save({'attempts':{preview.attempt_key:{'prepared':asdict(preview),'status':'unknown'}}})
    t=Fake(); send(normal,s,t)
    assert len(t.calls)==1 and not t.calls[0].preview_test
    assert s.read()['completed'][issue.issue_key]==normal.attempt_key


def test_legacy_pending_body_is_used_exactly_without_rerendering(tmp_path, monkeypatch):
    from dataclasses import asdict
    p = prepared(); saved = asdict(p)
    for lead in saved['issue']['leads']:
        for field in ('records_request_text', 'records_custodian', 'records_submission_url', 'records_route_note', 'caveat', 'records_route_evidence_ids'):
            lead.pop(field)
    saved['rendered'].update(html='<p>Legacy prepared body with internal rows</p>', text='Legacy prepared body\n')
    saved['fingerprint'] = saved['rendered']['fingerprint'] = 'old-render-fingerprint'
    s = StateStore(tmp_path, git=False)
    s.save({'attempts': {p.attempt_key: {'prepared': saved, 'status': 'pending'}}})
    monkeypatch.setattr('dochunt.delivery.render_issue', lambda *args: pytest.fail('Recovery re-rendered saved issue'))
    t = Fake(); send(p, s, t)
    assert t.calls[0].rendered == saved['rendered']
    assert t.calls[0].fingerprint == 'old-render-fingerprint'
    assert all('records_request_text' not in l for l in t.calls[0].issue['leads'])
