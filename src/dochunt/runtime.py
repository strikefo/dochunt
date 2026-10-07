"""Eligibility before paid calls; exact durable payload recovery."""
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .schedule import due
from .delivery import PreparedIssue, prepare, send, delivery_readiness, sender_header
from .models import Issue, Lead, Evidence, Usage
from .render import render_issue


def run(config,store,output_dir,*,provider=None,transport=None,now_utc=None,manual=False,research_only=False,delivery_only=False):
    clock=now_utc or datetime.now(timezone.utc)
    store.check_head()
    states=store.read(); decision=due(config,clock,states,manual=manual)
    if not decision.due: return decision
    for record in states['attempts'].values():
        saved=record['prepared']
        if not saved['preview_test'] and saved['issue']['issue_key']==decision.issue_key and record['status']!='accepted':
            saved=dict(saved); saved['recipient_snapshot']=tuple(saved['recipient_snapshot'])
            saved['seen_documents']=tuple(saved.get('seen_documents',()))
            return decision if research_only else send(PreparedIssue(**saved),store,transport)
    if not research_only:
        sender_header(config.email.sender,config.identity.sender_name)
        delivery_readiness(config.email.provider,transport)
    if not delivery_only:
        build(config,output_dir,states,clock,provider)
    if research_only: return decision
    return deliver_artifacts(config,store,output_dir,transport,decision.issue_key)


def build(config,output_dir,states,clock,provider):
    from .cli import preview
    history=Path(output_dir)/'history-input.jsonl'; history.parent.mkdir(parents=True,exist_ok=True)
    history.write_text(''.join(json.dumps(row)+'\n' for row in states['history']))
    try:
        preview(config,output_dir,provider=provider,history_path=history,local_date=clock.astimezone(ZoneInfo(config.schedule.timezone)).date(),
                seen=states.get('seen_documents',{}))
    finally: history.unlink(missing_ok=True)


def deliver_artifacts(config,store,output_dir,transport,issue_key):
    # The same manifest-checked loader as reviewed previews: every artifact is hashed,
    # and engine evidence is re-checked against the saved source texts.
    from .artifacts import load_preview
    issue,rendered=load_preview(output_dir,config,require_paid=True,require_send=True)
    if issue.issue_key!=issue_key: raise RuntimeError('Artifacts are not for the current local date')
    analysed=Path(output_dir)/'analysed-documents.json'
    seen=tuple(json.loads(analysed.read_text())) if analysed.exists() else ()
    return send(prepare(issue,rendered,config,seen_documents=seen),store,transport)
