from datetime import date
import pytest
from support import config, sample_issue
from dochunt.render import render_issue
from dochunt.delivery import prepare, send
from dochunt.state import StateStore


def prepared():
    c=config(); i=sample_issue(c,date(2026,10,2))
    return prepare(i,render_issue(i,c),c)


class Fake:
    def __init__(self): self.calls=[]
    def send(self,p):
        from dochunt.models import SendReceipt
        self.calls.append(p)
        return SendReceipt('resend','accepted',provider_id='fake',accepted_at='2026-10-02T12:00:00+00:00')


def test_failed_pending_prevents_send(tmp_path,monkeypatch):
    s=StateStore(tmp_path,git=False); t=Fake()
    monkeypatch.setattr(s,'save',lambda *a: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): send(prepared(),s,t)
    assert not t.calls


def test_acceptance_and_retry_snapshot(tmp_path):
    s=StateStore(tmp_path,git=False); t=Fake(); p=prepared()
    send(p,s,t); send(p,s,t)
    assert len(t.calls)==1
    assert s.read()['attempts'][p.attempt_key]['status']=='accepted'


def test_post_send_failure_recovers_exact_pending(tmp_path,monkeypatch):
    s=StateStore(tmp_path,git=False); t=Fake(); original=s.save; calls=0
    def save(data):
        nonlocal calls
        calls+=1
        if calls==3: raise OSError('disk')
        original(data)
    monkeypatch.setattr(s,'save',save)
    with pytest.raises(OSError): send(prepared(),s,t)
    monkeypatch.setattr(s,'save',original)
    send(prepared(),s,t)
    assert t.calls[0]==t.calls[1]


def test_an_issue_already_sent_is_not_delivered_again_without_resend(tmp_path):
    c=config(); i=sample_issue(c,date(2026,10,2)); r=render_issue(i,c)
    s=StateStore(tmp_path,git=False); t=Fake()
    send(prepare(i,r,c),s,t)
    with pytest.raises(RuntimeError, match='already sent'):
        send(prepare(i,r,c,attempt_id='second-try'),s,t)   # a new attempt key, but not an explicit resend
    send(prepare(i,r,c,attempt_id='extra-copy'),s,t,repeat=True)
    send(prepare(i,r,c,recipient=c.email.recipients[0]),s,t)   # test sends stay allowed
    assert len(t.calls)==3


def test_standalone_retry_must_match_the_saved_email(tmp_path):
    from dochunt.delivery import DefiniteNonSend
    c=config(); d=date(2026,10,2)
    saved=sample_issue(c,d,'no-leads'); chosen=sample_issue(c,d)
    s=StateStore(tmp_path,git=False)
    class Refuses:
        def send(self,p): raise DefiniteNonSend('not handed over')
    with pytest.raises(DefiniteNonSend): send(prepare(saved,render_issue(saved,c),c),s,Refuses())
    assert next(iter(s.read()['attempts'].values()))['status']=='pending'
    t=Fake()
    with pytest.raises(RuntimeError, match='holds a different email'):
        send(prepare(chosen,render_issue(chosen,c),c),s,t,match_saved=True)
    assert not t.calls
    send(prepare(saved,render_issue(saved,c),c),s,t,match_saved=True)   # the same email retries normally
    assert len(t.calls)==1


def test_preview_test_does_not_mark_scheduled_complete(tmp_path):
    from dataclasses import replace
    p=replace(prepared(),preview_test=True); s=StateStore(tmp_path,git=False)
    send(p,s,Fake())
    assert not s.read()['completed'] and not s.read()['leads']


def test_smtp_partial_and_timeout_stop_retry(tmp_path):
    from dataclasses import replace
    from dochunt.delivery import SMTPTransport
    p=replace(prepared(),provider='smtp')
    class Client:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def starttls(self,**kwargs): pass
        def login(self,*a): pass
        def send_message(self,*a,**k): return {p.recipient_snapshot[0]:(550,b'no')}
    t=SMTPTransport(username='fake',password='fake',factory=lambda *a,**k:Client())
    s=StateStore(tmp_path,git=False)
    assert send(p,s,t).state.value=='unknown'
    with pytest.raises(RuntimeError): send(p,s,t)
    class Timeout(Client):
        def send_message(self,*a,**k): raise TimeoutError()
    assert SMTPTransport(username='fake',password='fake',factory=lambda *a,**k:Timeout()).send(p).state.value=='unknown'


def test_resend_exact_wire_snapshot_and_key():
    import json
    from dochunt.delivery import ResendTransport
    p=prepared(); requests=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return json.dumps({'data':[{'id':f'id-{i}'} for i in range(len(p.recipient_snapshot))]}).encode()
    def opener(request,**kwargs): requests.append(request); return Response()
    t=ResendTransport(api_key='synthetic',opener=opener)
    assert t.send(p).accepted_recipients==list(p.recipient_snapshot)
    t.send(p)
    assert requests[0].data==requests[1].data
    assert requests[0].get_header('Idempotency-key')==p.attempt_key
    assert requests[0].full_url=='https://api.resend.com/emails/batch'
    body=json.loads(requests[0].data)
    # One email per recipient, each addressed only to that reader; no extra mailbox.
    assert [e['to'] for e in body]==[[r] for r in p.recipient_snapshot]
    assert all(e['subject']==p.rendered['subject'] and 'bcc' not in e for e in body)


def test_resend_per_recipient_batch_hides_addresses_and_adds_nobody():
    import json
    from dataclasses import replace
    from dochunt.delivery import ResendTransport
    p=replace(prepared(),recipient_snapshot=('a@example.org','b@example.org','c@example.org'))
    sent=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return b'{"data":[{"id":"1"},{"id":"2"},{"id":"3"}]}'
    def opener(request,**kwargs): sent.append(json.loads(request.data)); return Response()
    receipt=ResendTransport(api_key='synthetic',opener=opener).send(p)
    assert [e['to'] for e in sent[0]]==[['a@example.org'],['b@example.org'],['c@example.org']]
    assert receipt.provider_id=='1,2,3' and receipt.accepted_recipients==['a@example.org','b@example.org','c@example.org']
    class Short(Response):
        def read(self): return b'{"data":[{"id":"1"}]}'
    import pytest
    with pytest.raises(RuntimeError,match='Uncertain'):
        ResendTransport(api_key='synthetic',opener=lambda r,**k: Short()).send(p)


def test_attempts_saved_by_older_versions_replay_their_original_request():
    import json
    from dataclasses import asdict
    from dochunt.delivery import ResendTransport, restore
    saved=asdict(prepared()); saved.pop('wire'); saved.pop('subscription_url')
    legacy=restore(saved)
    assert legacy.wire==0
    seen=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return b'{"id":"legacy-id"}'
    def opener(request,**kwargs): seen.append(request); return Response()
    assert ResendTransport(api_key='synthetic',opener=opener).send(legacy).provider_id=='legacy-id'
    assert seen[0].full_url=='https://api.resend.com/emails' and json.loads(seen[0].data)['to']==list(legacy.recipient_snapshot)


def test_smtp_definite_failure_is_pending_and_can_recover(tmp_path):
    from dataclasses import replace
    from dochunt.delivery import SMTPTransport, DefiniteNonSend
    p=replace(prepared(),provider='smtp'); s=StateStore(tmp_path,git=False)
    def factory(*a,**k): raise OSError('connection refused')
    with pytest.raises(DefiniteNonSend): send(p,s,SMTPTransport(username='fake',password='fake',factory=factory))
    assert s.read()['attempts'][p.attempt_key]['status']=='pending'
    send(p,s,Fake())
    assert s.read()['attempts'][p.attempt_key]['status']=='accepted'


def test_sender_snapshot_headers_and_injection():
    from dataclasses import replace
    from dochunt.delivery import from_header, ResendTransport, SMTPTransport, DefiniteNonSend
    p=replace(prepared(),sender_name='Example "News"'); sent=[]
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return b'{"data":[{"id":"fake"}]}'
    def opener(req,**k):
        import json
        sent.append(json.loads(req.data)[0]['from']); return Response()
    ResendTransport(api_key='fake',opener=opener).send(p)
    class Client:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def starttls(self,**k): pass
        def login(self,*a): pass
        def send_message(self,msg,**k): sent.append(str(msg['From'])); return {}
    SMTPTransport(username='fake',password='fake',factory=lambda *a,**k:Client()).send(p)
    assert sent==[from_header(p)]*2
    with pytest.raises(DefiniteNonSend): from_header(replace(p,sender_name='Name\r\nBcc: bad@example.invalid'))


def test_old_snapshot_without_sender_name_recovers(tmp_path):
    from dataclasses import asdict
    p=prepared(); saved=asdict(p); saved.pop('sender_name')
    s=StateStore(tmp_path,git=False); state=s.read()
    state['attempts'][p.attempt_key]={'prepared':saved,'status':'pending'}; s.save(state)
    t=Fake(); send(p,s,t)
    assert t.calls[0].sender_name==''


def test_resend_explicit_user_agent_can_pass_gateway_without_changing_send():
    import json
    from urllib.error import HTTPError
    from dochunt.delivery import ResendTransport
    p=prepared()
    class Response:
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def read(self): return json.dumps({'data':[{'id':'synthetic-id'}]}).encode()
    def opener(request,**kwargs):
        agent=request.get_header('User-agent')
        if not agent or not agent.startswith('DocHunt/'):
            raise HTTPError(request.full_url,403,'Forbidden',{},None)
        assert request.get_method()=='POST'
        assert request.full_url=='https://api.resend.com/emails/batch'
        assert request.get_header('Idempotency-key')==p.attempt_key
        assert request.get_header('Authorization')=='Bearer synthetic-unused'
        payload=json.loads(request.data)[0]
        assert payload['to']==list(p.recipient_snapshot)
        assert payload['subject']==p.rendered['subject']
        assert payload['html']==p.rendered['html']
        assert payload['text']==p.rendered['text']
        return Response()
    receipt=ResendTransport(api_key='synthetic-unused',opener=opener).send(p)
    assert receipt.state.value=='accepted'
    assert receipt.provider_id=='synthetic-id'  # one recipient: one id
    assert receipt.accepted_recipients==list(p.recipient_snapshot)
