from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import pytest
from test_delivery import prepared, Fake
from support import config, provider as fictional_provider, NoCalls
from dochunt.delivery import send
from dochunt.state import PublishedStateStore, StateStore
from dochunt.runtime import run


def git(path,*args):
    return subprocess.run(['git',*map(str,args)],cwd=path,check=True,capture_output=True,text=True).stdout.strip()


def clone(remote,path):
    git(remote.parent,'clone',remote,path)
    git(path,'config','user.name','Test'); git(path,'config','user.email','test@example.invalid')
    return PublishedStateStore(path/'data',repo=path)


@pytest.fixture
def repos(tmp_path):
    remote=tmp_path/'remote.git'; git(tmp_path,'init','--bare','--initial-branch=main',remote)
    seed=tmp_path/'seed'; git(tmp_path,'clone',remote,seed)
    git(seed,'config','user.name','Test'); git(seed,'config','user.email','test@example.invalid')
    (seed/'data').mkdir(); (seed/'data'/'state.json').write_text('{}\n'); (seed/'data'/'history.jsonl').write_text('')
    git(seed,'add','data'); git(seed,'commit','-m','seed'); git(seed,'push','origin','main')
    return remote,clone(remote,tmp_path/'one')


def test_each_transition_published_before_transport(repos,tmp_path,monkeypatch):
    remote,s=repos; statuses=[]; original=s._git
    def call(*args,**kwargs):
        out=original(*args,**kwargs)
        if args[0]=='push':
            import json
            snapshot=json.loads(git(remote,'show','main:data/state.json'))
            statuses.append(next(iter(snapshot['attempts'].values()))['status'])
        return out
    monkeypatch.setattr(s,'_git',call)
    class Transport(Fake):
        def send(self,p):
            assert statuses==['pending','sending']
            return super().send(p)
    send(prepared(),s,Transport())
    assert statuses==['pending','sending','accepted']
    fresh=clone(remote,tmp_path/'fresh'); t=Fake(); send(prepared(),fresh,t)
    assert not t.calls and fresh.read()['completed']


def test_protected_branch_cannot_persist_pending(repos):
    remote,s=repos; hook=remote/'hooks'/'pre-receive'; hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o700)
    t=Fake()
    with pytest.raises(RuntimeError): send(prepared(),s,t)
    assert not t.calls and git(remote,'show','main:data/state.json')=='{}'


def test_competing_checkouts_nonfastforward_stops(repos,tmp_path,monkeypatch):
    remote,s=repos; rival=clone(remote,tmp_path/'rival'); original=s._git
    def call(*args,**kwargs):
        if args[0]=='push':
            competing=StateStore(rival.path,repo=rival.repo); state=competing.read(); state['marker']='rival'
            competing.save(state); git(rival.repo,'push','origin','main')
        return original(*args,**kwargs)
    monkeypatch.setattr(s,'_git',call); t=Fake()
    with pytest.raises(RuntimeError): send(prepared(),s,t)
    assert not t.calls and 'rival' in git(remote,'show','main:data/state.json')


@pytest.mark.parametrize('after_acceptance',[False,True])
def test_interrupted_runner_recovers_exact_saved_attempt(repos,tmp_path,monkeypatch,after_acceptance):
    remote,s=repos; original=s.save; n=0; initial=prepared(); t=Fake()
    def save(data):
        nonlocal n
        n+=1
        if after_acceptance and n==3: raise OSError('runner lost after acceptance')
        original(data)
    monkeypatch.setattr(s,'save',save)
    class Interrupted(Fake):
        def send(self,p): raise KeyboardInterrupt('runner lost before provider')
    with pytest.raises((KeyboardInterrupt,OSError)): send(initial,s,t if after_acceptance else Interrupted())
    fresh=clone(remote,tmp_path/'fresh')
    recovery=Fake(); c=config(); c.email.recipients=['changed@example.invalid']
    outcome=run(c,fresh,tmp_path/'output',provider=NoCalls(),transport=recovery,manual=True,now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc))
    assert outcome.state.value=='accepted' and recovery.calls==[initial]
    if after_acceptance: assert t.calls==[initial]


def test_unknown_smtp_stops_before_research(repos,tmp_path):
    _,s=repos; p=replace(prepared(),provider='smtp')
    class Unknown(Fake):
        def send(self,p):
            from dochunt.models import SendReceipt
            return SendReceipt('smtp','unknown')
    send(p,s,Unknown())
    decision=run(config(),s,tmp_path/'out',provider=NoCalls(),transport=NoCalls(),manual=True,now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc))
    assert not decision.due


def test_normal_watch_analysis_render_delivery(repos,tmp_path):
    _,s=repos; c=config(); p=fictional_provider(c)
    t=Fake(); receipt=run(c,s,tmp_path/'output',provider=p,transport=t,manual=True,now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc))
    assert receipt.state.value=='accepted' and len(t.calls)==1 and len(p.transport.requests)==2
    assert (tmp_path/'output'/'issue.json').exists()
    assert s.read()['seen_documents']


def test_disabled_has_no_paid_calls(repos,tmp_path):
    _,s=repos
    assert not run(config(),s,tmp_path/'out',provider=NoCalls(),now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc)).due


def test_split_stages_do_not_share_credentials(repos,tmp_path):
    _,s=repos; c=config(); t=Fake()
    clock=datetime(2026,10,2,14,tzinfo=timezone.utc)
    assert run(c,s,tmp_path/'out',provider=fictional_provider(c),transport=t,manual=True,research_only=True,now_utc=clock).due
    assert not t.calls and not s.read()['attempts']
    run(c,s,tmp_path/'out',transport=t,manual=True,delivery_only=True,now_utc=clock)
    assert len(t.calls)==1


def test_same_parent_content_and_timestamp_has_one_smtp_claimant(repos,tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import asdict
    import threading
    remote,s=repos
    monkeypatch.setenv('GIT_AUTHOR_DATE','2026-10-02T14:00:00+00:00')
    monkeypatch.setenv('GIT_COMMITTER_DATE','2026-10-02T14:00:00+00:00')
    p=replace(prepared(),provider='smtp')
    state=s.read(); state['attempts'][p.attempt_key]={'prepared':asdict(p),'status':'pending'}; s.save(state)
    rival=clone(remote,tmp_path/'rival')
    # Both checkouts must build sending from the same published pending parent.
    current=clone(remote,tmp_path/'current')
    barrier=threading.Barrier(2); push_lock=threading.Lock(); candidate_heads=[]
    for store in (current,rival):
        original=store._git; calls=[0]
        def call(*args,_original=original,_calls=calls,_store=store,**kwargs):
            if args[0]=='push':
                _calls[0]+=1
                if _calls[0]==1:
                    candidate_heads.append(_store.head)
                    barrier.wait(timeout=10)
                    try:
                        with push_lock: return _original(*args,**kwargs)
                    finally: barrier.wait(timeout=10)
            return _original(*args,**kwargs)
        monkeypatch.setattr(store,'_git',call)
    transport=Fake()
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(send,p,store,transport) for store in (current,rival)]
        failures=[]
        for future in futures:
            try: future.result(timeout=20)
            except RuntimeError as error: failures.append(error)
    assert len(transport.calls)==1
    assert len(set(candidate_heads))==2
    assert len(failures)==1

@pytest.mark.parametrize('provider_name',['smtp','resend'])
def test_missing_mail_credentials_before_research(tmp_path,monkeypatch,provider_name):
    for key in ('SMTP_USERNAME','SMTP_PASSWORD','RESEND_API_KEY'): monkeypatch.delenv(key,raising=False)
    c=config(); c.email.provider=provider_name
    s=StateStore(tmp_path/'state',git=False)
    with pytest.raises(ValueError,match='credentials'):
        run(c,s,tmp_path/'out',provider=NoCalls(),manual=True,now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc))
    assert not s.read()['attempts']


def test_recovery_readiness_uses_frozen_provider(tmp_path,monkeypatch):
    from dataclasses import asdict
    for key in ('SMTP_USERNAME','SMTP_PASSWORD'): monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('RESEND_API_KEY','synthetic')
    p=replace(prepared(),provider='smtp'); s=StateStore(tmp_path/'state',git=False)
    state=s.read(); state['attempts'][p.attempt_key]={'prepared':asdict(p),'status':'pending'}; s.save(state)
    with pytest.raises(ValueError,match='SMTP credentials'):
        run(config(),s,tmp_path/'out',manual=True,now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc))
    assert s.read()['attempts'][p.attempt_key]['status']=='pending'


def test_invalid_sender_before_research(tmp_path):
    c=config(); c.identity.sender_name='News\nBcc: bad@example.invalid'
    with pytest.raises(ValueError,match='From header'):
        run(c,StateStore(tmp_path/'state',git=False),tmp_path/'out',provider=NoCalls(),transport=Fake(),manual=True,now_utc=datetime(2026,10,2,14,tzinfo=timezone.utc))
