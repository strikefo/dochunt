"""Provider-neutral, credential-free application records.

The agent returns JSON matching ``schema/issue.schema.json``. ``parse_issue_data``
turns it into these records and reports every structural problem at once, so a
single repair request to the agent can fix them all.
"""
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ACTIONS = ('foia', 'inquiry', 'download')
NO_LEAD_SUBJECT = 'No actionable records leads qualified today'
EMAIL_RE = re.compile(r'[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[A-Za-z]{2,}')


def public_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("URL must be a nonempty string")
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected an HTTP(S) URL without credentials')
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('URL contains whitespace or control characters')
    return value


def source_key(url: str) -> str:
    """Normalized identity of a source document for deduplication. Never raises."""
    try:
        parts = urlsplit(url.strip())
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        return url.strip().lower()
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not k.lower().startswith('utm_')))
    path = parts.path.rstrip('/') or '/'
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    if ':' in host:
        host = f'[{host}]'
    if parts.port and parts.port not in (80, 443):
        host = f'{host}:{parts.port}'
    # '#/records/123' or '#!/doc/9' is a single-page app's route and identifies the document;
    # an ordinary '#section' anchor does not.
    fragment = parts.fragment if parts.fragment.startswith(('/', '!')) else ''
    return urlunsplit(('https', host, path, query, fragment))


class IssueState(StrEnum):
    PREPARED = 'prepared'
    ACCEPTED = 'accepted'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


@dataclass
class Contact:
    label: str = ''
    url: str = ''
    email: str = ''
    phone: str = ''


@dataclass
class GetRecords:
    agency: str
    office: str
    action: str
    contact: Contact
    verified_at_url: str
    text: str


@dataclass
class Lead:
    headline: str
    reporting_opportunity: str
    source_title: str
    source_url: str
    get_records: GetRecords
    caveat: str = ''
    newsworthiness: int = 5
    obtainability: int = 5
    is_update: bool = False
    reviewer_notes: str = ''

    def __post_init__(self):
        if isinstance(self.get_records, dict):
            data = dict(self.get_records)
            data['contact'] = Contact(**data.get('contact', {}))
            self.get_records = GetRecords(**data)


@dataclass
class Issue:
    issue_key: str
    local_date: date
    subject: str
    intro: str
    no_lead_reason: str
    leads: list[Lead]

    def __post_init__(self):
        if not isinstance(self.issue_key, str) or not self.issue_key.strip() or type(self.local_date) is not date:
            raise ValueError('Issue key and local date are required')
        if not isinstance(self.leads, list) or any(not isinstance(l, Lead) for l in self.leads):
            raise ValueError('Issue leads must be Lead records')
        problems = issue_problems(self)
        if problems:
            raise ValueError('; '.join(problems))


class IssueFormatError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__('; '.join(self.problems))


def _text(value, where, problems, *, required=True):
    if not isinstance(value, str):
        problems.append(f'{where} must be a string')
        return ''
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        problems.append(f'{where} contains control characters')
    if required and not value.strip():
        problems.append(f'{where} is empty')
    return value.strip()


def _object(value, keys, where, problems):
    if not isinstance(value, dict):
        problems.append(f'{where} must be an object')
        return {}
    extra = set(value) - set(keys)
    missing = set(keys) - set(value)
    if extra:
        problems.append(f'{where} has unexpected fields: {", ".join(sorted(extra))}')
    if missing:
        problems.append(f'{where} is missing fields: {", ".join(sorted(missing))}')
    return value


def parse_lead(data, where='lead'):
    """Return (lead or None, problems)."""
    problems = []
    data = _object(data, ('headline', 'reporting_opportunity', 'source', 'get_records', 'caveat', 'internal'), where, problems)
    source = _object(data.get('source', {}), ('title', 'url'), f'{where}.source', problems)
    records = _object(data.get('get_records', {}), ('agency', 'office', 'action', 'contact', 'verified_at_url', 'text'), f'{where}.get_records', problems)
    contact = _object(records.get('contact', {}), ('label', 'url', 'email', 'phone'), f'{where}.get_records.contact', problems)
    internal = _object(data.get('internal', {}), ('newsworthiness', 'obtainability', 'is_update', 'reviewer_notes'), f'{where}.internal', problems)
    action = records.get('action')
    if action not in ACTIONS:
        problems.append(f'{where}.get_records.action must be one of {", ".join(ACTIONS)}')
    scores = []
    for name in ('newsworthiness', 'obtainability'):
        value = internal.get(name)
        if type(value) is not int or not 1 <= value <= 10:
            problems.append(f'{where}.internal.{name} must be an integer 1-10')
            value = 5
        scores.append(value)
    if type(internal.get('is_update', False)) is not bool:
        problems.append(f'{where}.internal.is_update must be true or false')
    lead = Lead(
        headline=_text(data.get('headline'), f'{where}.headline', problems),
        reporting_opportunity=_text(data.get('reporting_opportunity'), f'{where}.reporting_opportunity', problems),
        source_title=_text(source.get('title'), f'{where}.source.title', problems),
        source_url=_text(source.get('url'), f'{where}.source.url', problems),
        get_records=GetRecords(
            agency=_text(records.get('agency'), f'{where}.get_records.agency', problems),
            office=_text(records.get('office'), f'{where}.get_records.office', problems, required=False),
            action=action if action in ACTIONS else 'foia',
            contact=Contact(**{k: _text(contact.get(k, ''), f'{where}.get_records.contact.{k}', problems, required=False)
                               for k in ('label', 'url', 'email', 'phone')}),
            verified_at_url=_text(records.get('verified_at_url', ''), f'{where}.get_records.verified_at_url', problems, required=False),
            text=_text(records.get('text'), f'{where}.get_records.text', problems),
        ),
        caveat=_text(data.get('caveat', ''), f'{where}.caveat', problems, required=False),
        newsworthiness=scores[0], obtainability=scores[1],
        is_update=internal.get('is_update') is True,
        reviewer_notes=_text(internal.get('reviewer_notes', ''), f'{where}.internal.reviewer_notes', problems, required=False),
    )
    return (None if problems else lead), problems


def lead_problems(lead: Lead, where='lead'):
    """Per-lead rules a reader relies on: real links and a usable first action."""
    problems = []
    r = lead.get_records
    c = r.contact
    try:
        public_url(lead.source_url)
    except ValueError:
        problems.append(f'{where}: source link is not a valid web address')
    for name, value in (('contact link', c.url), ('verification page', r.verified_at_url)):
        if value:
            try:
                public_url(value)
            except ValueError:
                problems.append(f'{where}: {name} is not a valid web address')
    if c.email and not EMAIL_RE.fullmatch(c.email):
        problems.append(f'{where}: contact email is not a valid address')
    if c.phone and not re.search(r'\d{3}.*\d{4}', c.phone):
        problems.append(f'{where}: contact phone is not a phone number')
    if r.action == 'foia' and not (c.url or c.email):
        problems.append(f'{where}: a records request needs a submission link or email')
    if r.action == 'inquiry' and not (c.url or c.email or c.phone):
        problems.append(f'{where}: an inquiry needs an email, phone or contact link')
    if r.action == 'download' and not c.url:
        problems.append(f'{where}: a download needs a direct link')
    if r.action in ('foia', 'inquiry') and not r.verified_at_url:
        problems.append(f'{where}: name the official page where the contact or route was verified')
    if len(lead.headline) > 160:
        problems.append(f'{where}: headline is longer than 160 characters')
    return problems


def subject_problems(subject: str):
    problems = []
    if not isinstance(subject, str) or not subject.strip():
        problems.append('subject is empty')
    elif any(ord(c) < 32 or ord(c) == 127 for c in subject) or len(subject.strip()) > 100:
        problems.append('subject must be one line under 100 characters, without control characters')
    return problems


def issue_problems(issue: Issue):
    problems = subject_problems(issue.subject)
    for i, lead in enumerate(issue.leads, 1):
        problems += lead_problems(lead, f'lead {i}')
    if not issue.leads and not issue.no_lead_reason.strip():
        problems.append('an issue without leads needs a short no-lead reason')
    if issue.leads and issue.no_lead_reason.strip():
        problems.append('no_lead_reason must be empty when there are leads')
    keys = [source_key(l.source_url) for l in issue.leads]
    if len(set(keys)) != len(keys):
        problems.append('two leads share the same source document')
    return problems


def parse_issue_data(data, *, issue_key, local_date):
    """Parse agent JSON into an Issue, raising IssueFormatError with every problem."""
    problems = []
    if isinstance(data, dict) and 'checked_sources' in data:
        data = {k: v for k, v in data.items() if k != 'checked_sources'}  # research record, not part of the email
    data = _object(data, ('subject', 'intro', 'no_lead_reason', 'leads'), 'issue', problems)
    leads_data = data.get('leads', [])
    if not isinstance(leads_data, list):
        problems.append('issue.leads must be a list')
        leads_data = []
    leads = []
    for i, item in enumerate(leads_data, 1):
        lead, found = parse_lead(item, f'lead {i}')
        problems += found
        if lead:
            leads.append(lead)
    subject = _text(data.get('subject', ''), 'issue.subject', problems)
    intro = _text(data.get('intro', ''), 'issue.intro', problems, required=False)
    reason = _text(data.get('no_lead_reason', ''), 'issue.no_lead_reason', problems, required=False)
    if problems:
        raise IssueFormatError(problems + [p for p in subject_problems(subject) if p not in problems])
    return issue_from_parts(issue_key, local_date, subject, intro, reason, leads)


def issue_from_parts(issue_key, local_date, subject, intro, no_lead_reason, leads):
    """Build an Issue, raising IssueFormatError (not a bare ValueError) on rule failures."""
    probe = Issue.__new__(Issue)
    probe.issue_key, probe.local_date, probe.subject, probe.intro, probe.no_lead_reason, probe.leads = (
        issue_key, local_date, subject, intro, no_lead_reason, leads)
    problems = issue_problems(probe)
    if problems:
        raise IssueFormatError(problems)
    return Issue(issue_key, local_date, subject, intro, no_lead_reason, leads)


def issue_to_data(issue: Issue) -> dict:
    """Agent-schema JSON for an issue (round-trips through parse_issue_data)."""
    return {
        'subject': issue.subject, 'intro': issue.intro, 'no_lead_reason': issue.no_lead_reason,
        'leads': [{
            'headline': l.headline, 'reporting_opportunity': l.reporting_opportunity,
            'source': {'title': l.source_title, 'url': l.source_url},
            'get_records': {'agency': l.get_records.agency, 'office': l.get_records.office, 'action': l.get_records.action,
                            'contact': {'label': l.get_records.contact.label, 'url': l.get_records.contact.url,
                                        'email': l.get_records.contact.email, 'phone': l.get_records.contact.phone},
                            'verified_at_url': l.get_records.verified_at_url, 'text': l.get_records.text},
            'caveat': l.caveat,
            'internal': {'newsworthiness': l.newsworthiness, 'obtainability': l.obtainability,
                         'is_update': l.is_update, 'reviewer_notes': l.reviewer_notes},
        } for l in issue.leads],
    }


@dataclass
class SendReceipt:
    provider: str
    state: IssueState
    provider_id: str | None = None
    accepted_at: str | None = None
    error: str | None = None

    accepted_recipients: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.state = IssueState(self.state)
        if self.provider not in {'resend','smtp'}:
            raise ValueError('Unsupported delivery provider')
        if self.state == IssueState.ACCEPTED and not self.accepted_at:
            raise ValueError('Accepted receipt requires acceptance timestamp')
