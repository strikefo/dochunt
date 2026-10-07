"""Durable pending-before-send delivery; accepted is not inbox delivery."""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
import hashlib
import json
import os
import re
import smtplib
import ssl
import urllib.request
from .models import SendReceipt
from .render import render_issue
from .editorial import validate_issue, lead_identity, lead_revision


def now(): return datetime.now(timezone.utc).isoformat()

@dataclass(frozen=True)
class PreparedIssue:
    issue: dict
    rendered: dict
    recipient_snapshot: tuple[str,...]
    fingerprint: str
    attempt_key: str
    prepared_at: str
    provider: str
    sender: str
    smtp_host: str = ''
    smtp_port: int = 587
    preview_test: bool = False
    sender_name: str = ''
    seen_documents: tuple[str,...] = ()   # watch mode: recorded as seen only once accepted

    def __post_init__(self):
        # Saved attempts round-trip through JSON, which turns tuples into lists.
        object.__setattr__(self,'seen_documents',tuple(self.seen_documents))


def prepare(issue,rendered,config,*,attempt_id=None,recipient=None,seen_documents=()):
    validate_issue(issue,config)
    if render_issue(issue,config)!=rendered: raise ValueError('Final render does not match issue')
    if attempt_id is not None and not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',attempt_id): raise ValueError('Invalid operator attempt ID')
    if recipient is not None and recipient not in config.email.recipients: raise ValueError('Select one configured recipient')
    data=asdict(issue); data['local_date']=issue.local_date.isoformat()
    key=f'{config.identity.id}/{issue.local_date.isoformat()}'
    if attempt_id: key+=f'/resend/{attempt_id}'
    if recipient: key+=f'/preview/{attempt_id or rendered.fingerprint}/{recipient}'
    return PreparedIssue(data,asdict(rendered),tuple([recipient] if recipient else config.email.recipients),rendered.fingerprint,hashlib.sha256(key.encode()).hexdigest(),now(),config.email.provider,config.email.sender,config.email.smtp_host,config.email.smtp_port,recipient is not None,config.identity.sender_name,tuple(seen_documents))


class DefiniteNonSend(ValueError):
    """Transport failed before handing any message to the provider."""


def delivery_readiness(provider, transport=None):
    transport=transport or (ResendTransport() if provider=='resend' else SMTPTransport())
    # Injected library transports own their readiness contract; simple mocks work.
    if hasattr(transport,'check_ready'): transport.check_ready()
    return transport


def sender_header(sender, sender_name=''):
    if any(ord(c)<32 or ord(c)==127 for c in sender_name+sender):
        raise DefiniteNonSend('Invalid From header')
    try: return formataddr((sender_name,sender)) if sender_name else sender
    except (UnicodeError,ValueError) as error: raise DefiniteNonSend('Invalid From address encoding') from error


def from_header(p):
    return sender_header(p.sender,p.sender_name)


def send(prepared,store,transport=None):
    with store.locked():
        data=store.read(); attempts=data.setdefault('attempts',{}); record=attempts.get(prepared.attempt_key)
        if record:
            saved=record['prepared']; saved['recipient_snapshot']=tuple(saved['recipient_snapshot']); prepared=PreparedIssue(**saved)
            if record['status']=='accepted': return SendReceipt(**record['receipt'])
            if record['status']=='unknown' or (record['status']=='sending' and prepared.provider=='smtp'):
                raise RuntimeError('Uncertain delivery requires manual recovery')
            if datetime.now(timezone.utc)-datetime.fromisoformat(prepared.prepared_at)>=timedelta(hours=24): raise RuntimeError('Idempotency window expired; manual recovery required')
        transport=delivery_readiness(prepared.provider,transport)
        from_header(prepared)
        if not record:
            if any(r['status']!='accepted' and r['prepared']['issue']['issue_key']==prepared.issue['issue_key'] and r['prepared']['preview_test']==prepared.preview_test for r in attempts.values()):
                raise RuntimeError('Resolve existing uncertain attempt before creating another')
            record={'prepared':asdict(prepared),'status':'pending'}; attempts[prepared.attempt_key]=record
            store.save(data)
        record['status']='sending'; store.save(data)
        store.check_head()
        try: receipt=transport.send(prepared)
        except DefiniteNonSend:
            record['status']='pending'; store.save(data)
            raise
        except Exception:
            record['status']='unknown' if prepared.provider=='smtp' else 'sending'; store.save(data)
            raise
        record['receipt']=asdict(receipt); record['status']=receipt.state.value
        if receipt.state.value=='accepted' and not prepared.preview_test:
            data.setdefault('completed',{})[prepared.issue['issue_key']]=prepared.attempt_key
            history=data.setdefault('history',[])
            from .models import Lead
            for lead in prepared.issue['leads']:
                lead=Lead(**lead); row={'identity':lead_identity(lead),'revision':lead_revision(lead)}
                if row not in history: history.append(row)
            if prepared.seen_documents:
                # Watch mode: documents analysed for this issue are skipped by later runs.
                seen=data.setdefault('seen_documents',{})
                for url in prepared.seen_documents: seen.setdefault(url,prepared.issue['local_date'])
                # Keep 180 days, far beyond any freshness window, so state stays small.
                cutoff=(date.fromisoformat(prepared.issue['local_date'])-timedelta(days=180)).isoformat()
                data['seen_documents']={u:d for u,d in seen.items() if d>=cutoff}
        store.save(data)
        return receipt


class ResendTransport:
    def __init__(self,*,api_key=None,opener=None):
        self.api_key=api_key or os.environ.get('RESEND_API_KEY'); self.opener=opener or urllib.request.urlopen
    def check_ready(self):
        if not self.api_key: raise DefiniteNonSend('Missing email credentials')
    def send(self,p):
        self.check_ready()
        payload={'from':from_header(p),'to':list(p.recipient_snapshot),**{k:p.rendered[k] for k in ('subject','html','text')}}
        request=urllib.request.Request('https://api.resend.com/emails',data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {self.api_key}','Content-Type':'application/json','Idempotency-Key':p.attempt_key,'User-Agent':'DocHunt/0.1'},method='POST')
        with self.opener(request,timeout=30) as response: result=json.loads(response.read())
        if not isinstance(result.get('id'),str): raise RuntimeError('Uncertain provider response')
        return SendReceipt('resend','accepted',provider_id=result['id'],accepted_at=now(),accepted_recipients=list(p.recipient_snapshot))


class SMTPTransport:
    def __init__(self,*,username=None,password=None,factory=None):
        self.username=username or os.environ.get('SMTP_USERNAME'); self.password=password or os.environ.get('SMTP_PASSWORD'); self.factory=factory or smtplib.SMTP
    def check_ready(self):
        if not self.username or not self.password: raise DefiniteNonSend('Missing SMTP credentials')
    def send(self,p):
        self.check_ready()
        msg=EmailMessage(); msg['From']=from_header(p); msg['To']='undisclosed-recipients:;'; msg['Subject']=p.rendered['subject']; msg['Message-ID']=f'<{p.attempt_key}@dochunt.invalid>'
        msg.set_content(p.rendered['text']); msg.add_alternative(p.rendered['html'],subtype='html')
        handed_off=False
        try:
            with self.factory(p.smtp_host,p.smtp_port,timeout=30) as client:
                client.starttls(context=ssl.create_default_context()); client.login(self.username,self.password)
                handed_off=True
                rejected=client.send_message(msg,from_addr=p.sender,to_addrs=list(p.recipient_snapshot))
            accepted=[r for r in p.recipient_snapshot if r not in rejected]
            if rejected: return SendReceipt('smtp','unknown',error='Partial recipient acceptance; manual recovery required',accepted_recipients=accepted)
            return SendReceipt('smtp','accepted',accepted_at=now(),accepted_recipients=accepted)
        except Exception as error:
            if not handed_off: raise DefiniteNonSend('SMTP failed before message handoff') from error
            return SendReceipt('smtp','unknown',error='SMTP result uncertain; manual recovery required')
