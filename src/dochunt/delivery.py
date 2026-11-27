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
from .models import SendReceipt, issue_problems, issue_to_data, parse_issue_data
from .render import render_issue
from .tracker import record_issue


RESEND_MAX_RECIPIENTS=100  # Resend batch API: up to 100 individually addressed emails per request
WIRE_VERSION=2


def check_recipient_limit(provider,recipients):
    if provider=='resend' and len(recipients)>RESEND_MAX_RECIPIENTS:
        raise ValueError(f'Resend accepts at most {RESEND_MAX_RECIPIENTS} recipients per send; this list has {len(recipients)}')


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
    # Saved with each attempt so a retry replays exactly what was first sent:
    # wire 0 = legacy single Resend email to everyone; 2 = one email per recipient (batch).
    wire: int = 0
    subscription_url: str = ''

    def __post_init__(self):
        # Saved attempts round-trip through JSON, which turns tuples into lists.
        object.__setattr__(self,'recipient_snapshot',tuple(self.recipient_snapshot))


def restore(saved):
    """Rebuild a saved attempt exactly, ignoring fields from older versions (such as 0.1's seen_documents)."""
    from dataclasses import fields
    known={f.name for f in fields(PreparedIssue)}
    return PreparedIssue(**{k:v for k,v in saved.items() if k in known})


def prepare(issue,rendered,config,*,attempt_id=None,recipient=None,subscriptions=None):
    """Freeze one exact send. With a subscription list, recipients and the manage link are read now."""
    problems=issue_problems(issue)
    if problems: raise ValueError('Issue failed checks: '+'; '.join(problems))
    if render_issue(issue,config)!=rendered: raise ValueError('Final render does not match issue')
    if attempt_id is not None and not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',attempt_id): raise ValueError('Invalid operator attempt ID')
    if recipient is not None and recipient not in config.email.recipients: raise ValueError('Select one configured recipient')
    data={'issue_key':issue.issue_key,'local_date':issue.local_date.isoformat(),'issue':issue_to_data(issue)}
    key=f'{config.identity.id}/{issue.local_date.isoformat()}'
    if attempt_id: key+=f'/resend/{attempt_id}'
    if recipient: key+=f'/preview/{attempt_id or rendered.fingerprint}/{recipient}'
    recipients=[recipient] if recipient else list(config.email.recipients)
    if config.email.subscription_url and not recipient:
        from .subscriptions import fetch
        recipients=(subscriptions or fetch)(config.email)  # never the static list: an opt-out must hold
    check_recipient_limit(config.email.provider,recipients)
    return PreparedIssue(data,asdict(rendered),tuple(recipients),rendered.fingerprint,hashlib.sha256(key.encode()).hexdigest(),now(),config.email.provider,config.email.sender,config.email.smtp_host,config.email.smtp_port,recipient is not None,config.identity.sender_name,
                         wire=WIRE_VERSION,subscription_url=config.email.subscription_url if not recipient else '')


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


def send(prepared,store,transport=None,*,recheck=None,repeat=False,match_saved=False):
    """recheck(prepared) runs before retrying a saved, unaccepted subscription send (opt-outs since).

    A new production attempt for an issue that was already sent is refused unless repeat is set,
    which only an explicit resend does. With match_saved (the standalone commands), an unfinished
    saved attempt is retried only when it holds exactly the email the operator selected."""
    with store.locked():
        data=store.read(); attempts=data.setdefault('attempts',{}); record=attempts.get(prepared.attempt_key)
        if record:
            selected=prepared; prepared=restore(record['prepared'])
            if record['status']=='accepted': return SendReceipt(**record['receipt'])
            if match_saved and (prepared.fingerprint!=selected.fingerprint or prepared.rendered!=selected.rendered):
                raise RuntimeError('A saved, unfinished attempt for this issue holds a different email; reconcile it manually before sending')
            if record['status']=='unknown' or (record['status']=='sending' and prepared.provider=='smtp'):
                raise RuntimeError('Uncertain delivery requires manual recovery')
            if datetime.now(timezone.utc)-datetime.fromisoformat(prepared.prepared_at)>=timedelta(hours=24): raise RuntimeError('Idempotency window expired; manual recovery required')
            if not prepared.preview_test:
                if recheck is None and prepared.subscription_url:
                    raise RuntimeError('A saved subscription send needs its list re-checked before retry; reconcile manually')
                if recheck is not None: recheck(prepared)
        transport=delivery_readiness(prepared.provider,transport)
        from_header(prepared)
        if not record:
            if not prepared.preview_test and not repeat and prepared.issue['issue_key'] in data.get('completed',{}):
                raise RuntimeError('This issue was already sent; use resend with a new attempt ID for an intentional extra send')
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
            # The tracker is updated in the same atomic state commit that records acceptance.
            # A tracker problem must never prevent saving the acceptance itself (that would invite a resend).
            try:
                saved=prepared.issue
                issue=parse_issue_data(saved['issue'],issue_key=saved['issue_key'],local_date=date.fromisoformat(saved['local_date']))
                data['leads']=record_issue(data.get('leads',[]),issue)
            except (ValueError,KeyError,TypeError):
                record['tracker_error']='Sent leads could not be added to the tracker'
        store.save(data)
        return receipt


class ResendTransport:
    def __init__(self,*,api_key=None,opener=None):
        self.api_key=api_key or os.environ.get('RESEND_API_KEY'); self.opener=opener or urllib.request.urlopen
    def check_ready(self):
        if not self.api_key: raise DefiniteNonSend('Missing email credentials')
    def send(self,p):
        self.check_ready()
        content={k:p.rendered[k] for k in ('subject','html','text')}
        if p.wire>=2:
            # One email per recipient: nobody sees another reader's address and nobody else gets a copy.
            url='https://api.resend.com/emails/batch'
            body=[{'from':from_header(p),'to':[r],**content} for r in p.recipient_snapshot]
        else:
            # Attempts saved before per-recipient sending replay their original request exactly,
            # so the provider's idempotency key still matches.
            url='https://api.resend.com/emails'
            body={'from':from_header(p),'to':list(p.recipient_snapshot),**content}
        request=urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Authorization':f'Bearer {self.api_key}','Content-Type':'application/json','Idempotency-Key':p.attempt_key,'User-Agent':'DocHunt/0.2'},method='POST')
        with self.opener(request,timeout=30) as response: result=json.loads(response.read())
        if p.wire>=2:
            items=result.get('data') if isinstance(result,dict) else None
            if not isinstance(items,list) or len(items)!=len(p.recipient_snapshot) or any(not isinstance(i,dict) or not isinstance(i.get('id'),str) for i in items):
                raise RuntimeError('Uncertain provider response')
            provider_id=','.join(i['id'] for i in items)
        else:
            if not isinstance(result.get('id'),str): raise RuntimeError('Uncertain provider response')
            provider_id=result['id']
        return SendReceipt('resend','accepted',provider_id=provider_id,accepted_at=now(),accepted_recipients=list(p.recipient_snapshot))


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
