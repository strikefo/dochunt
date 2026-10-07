from dataclasses import replace
from datetime import datetime, timezone
import json
import pytest
from support import config, provider as fictional_provider, NoCalls
from test_delivery import Fake
from test_runtime import repos
from dochunt.cli import preview
from dochunt.runtime import run
from dochunt.artifacts import load_preview
from dochunt.delivery import prepare, send

@pytest.fixture(autouse=True)
def no_external_endpoints(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**k:pytest.fail('External/publisher endpoint requested'))


def newsroom(kind):
    c=config(); c.ai.provider=kind; c.ai.model='claude-sonnet-5-5' if kind=='anthropic' else 'gpt-6.1-sol'
    return c


@pytest.mark.parametrize('kind',['openai','anthropic'])
@pytest.mark.parametrize('empty',[False,True])
def test_preview_one_or_no_leads_with_each_provider(tmp_path,kind,empty):
    c=newsroom(kind)
    outputs=[{'leads':[],'no_lead_reason':'Nothing worth requesting.'}] if empty else None
    p=fictional_provider(c,outputs,kind)
    preview(c,tmp_path,provider=p,local_date=datetime(2026,10,2).date())
    i,r=load_preview(tmp_path,c,require_paid=True)
    assert len(i.leads)==(0 if empty else 1)
    assert len(p.transport.requests)==(1 if empty else 2)
    if kind=='anthropic':  # no native schema: the engine puts it in the instructions
        assert 'matching this JSON schema' in p.transport.requests[0]['system']


@pytest.mark.parametrize('kind',['openai','anthropic'])
def test_test_send_then_delivery_then_next_day_and_resend(repos,tmp_path,kind):
    _,store=repos; c=newsroom(kind); t=Fake(); clock=datetime(2026,10,2,14,tzinfo=timezone.utc)
    preview(c,tmp_path/'preview',provider=fictional_provider(c,None,kind),local_date=clock.date())
    i,r=load_preview(tmp_path/'preview',c,require_paid=True)
    send(prepare(i,r,c,recipient=c.email.recipients[0],attempt_id='test1'),store,t)
    assert not store.read()['completed'] and not store.read().get('seen_documents')  # a test send records nothing
    receipt=run(c,store,tmp_path/'normal',provider=fictional_provider(c,None,kind),transport=t,manual=True,now_utc=clock)
    assert receipt.state.value=='accepted' and len(t.calls)==2 and store.read()['seen_documents']
    assert not run(c,store,tmp_path/'repeat',provider=NoCalls(),transport=t,manual=True,now_utc=clock).due
    # Next day the document has been seen: no model call, an honest no-lead email.
    quiet=fictional_provider(c,[],kind)
    run(c,store,tmp_path/'next',provider=quiet,transport=t,manual=True,now_utc=clock.replace(day=3))
    assert not t.calls[-1].issue['leads'] and quiet.transport.requests==[]
    # Explicit operator resend has a separate attempt but preserves the reviewed payload.
    resend=prepare(i,r,c,attempt_id='operator-1')
    send(resend,store,t); send(resend,store,t)
    assert len(t.calls)==4


def test_interrupted_send_recovers_without_new_analysis(repos,tmp_path):
    _,store=repos; c=config(); clock=datetime(2026,10,2,14,tzinfo=timezone.utc)
    class Lost(Fake):
        def send(self,p): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): run(c,store,tmp_path/'out',provider=fictional_provider(c),transport=Lost(),manual=True,now_utc=clock)
    t=Fake(); run(c,store,tmp_path/'out',provider=NoCalls(),transport=t,manual=True,now_utc=clock)
    assert len(t.calls)==1


def test_direct_github_modes_and_safe_inputs():
    from pathlib import Path
    w=Path('.github/workflows/document-hunt.yml').read_text()
    for mode in ['paid-ai-preview','send-preview-test','resend']: assert mode in w
    assert 'download-preview' in w and 'actions: read' in w
    assert '${{ github.event.inputs.recipient }}' not in w.split('run:')[-1]


def test_workflow_credential_scopes():
    from pathlib import Path
    w=Path('.github/workflows/document-hunt.yml').read_text()
    paid=w.split('- name: Paid AI preview')[1].split('- name: Select previous')[0]
    selected=w.split('- name: Explicit selected-preview test')[1].split('- name: Reviewed paid')[0]
    assert 'OPENAI_API_KEY' in paid and 'RESEND_API_KEY' not in paid and 'SMTP_PASSWORD' not in paid
    assert 'RESEND_API_KEY' in selected and 'OPENAI_API_KEY' not in selected and 'ANTHROPIC_API_KEY' not in selected
    assert w.count('group: document-hunt-state')==1
