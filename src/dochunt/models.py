"""Provider-neutral, credential-free application records."""
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
import re
from urllib.parse import urlsplit

FIELD_PATCH_FIELDS=('provisional_headline','factual_hook','material_update','caveat','records_route_note','acquisition_details','records_request_text')


def public_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("URL must be a nonempty string")
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Expected an HTTP(S) URL without credentials')
    return value


class IssueState(StrEnum):
    PREPARED = 'prepared'
    ACCEPTED = 'accepted'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


@dataclass
class Limits:
    workflow_timeout_minutes: int = 30
    application_deadline_minutes: int = 25
    max_candidates: int = 20
    max_final_leads: int = 8
    research_requests: int = 12
    review_requests: int = 7
    searches: int = 20
    fetches: int = 30
    input_tokens: int = 120000
    output_tokens: int = 24000
    transient_retries: int = 2

    def __post_init__(self):
        for name, value in vars(self).items():
            if type(value) is not int or value < (0 if name == 'transient_retries' else 1):
                raise ValueError(f'Invalid limit: {name}')
        if self.application_deadline_minutes >= self.workflow_timeout_minutes:
            raise ValueError('Application deadline must precede workflow timeout')
        if self.max_final_leads > self.max_candidates:
            raise ValueError('Final leads cannot exceed candidates')


@dataclass
class Usage:
    research_requests: int = 0
    review_requests: int = 0
    searches: int = 0
    fetches: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in vars(self).values()):
            raise ValueError('Usage must contain nonnegative integers')


@dataclass
class Evidence:
    id: str
    url: str
    title: str
    excerpt: str
    retrieved_at: str
    content_hash: str = ''
    locator: str = ''
    format: str = ''
    verified: bool = False

    def __post_init__(self):
        public_url(self.url)
        if any(not isinstance(getattr(self,n),str) or not getattr(self,n).strip() for n in ('id','url','title','excerpt','retrieved_at')):
            raise ValueError('Evidence fields must be nonempty strings')
        if type(self.verified) is not bool or any(not isinstance(getattr(self,n),str) for n in ('content_hash','locator','format')):
            raise ValueError('Invalid evidence receipt')
        if self.verified and (not re.fullmatch(r'[0-9a-f]{64}',self.content_hash) or not self.locator or not self.format):
            raise ValueError('Verified evidence requires a complete receipt')


@dataclass
class Lead:
    id: str
    provisional_headline: str
    story_potential: str
    factual_hook: str
    evidence_ids: list[str]
    source_url: str
    likely_records: list[str]
    acquisition_route: str
    acquisition_details: str
    newsworthiness: int
    obtainability: int
    next_action: str
    reviewer_verdict: str
    jurisdiction: str = ''
    entities: list[str] = field(default_factory=list)
    material_update: str = ''
    records_request_text: str = ''
    records_custodian: str = ''
    records_submission_url: str = ''
    records_route_note: str = ''
    caveat: str = ''
    records_route_evidence_ids: list[str] = field(default_factory=list)
    records_plan: dict = field(default_factory=dict)
    hunt_plan: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.hunt_plan,dict): raise ValueError('Hunt plan must be an object')
        if not isinstance(self.records_plan,dict): raise ValueError('Records plan must be an object')
        for name in ('evidence_ids', 'likely_records', 'entities', 'records_route_evidence_ids'):
            value = getattr(self, name)
            if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError('Lead lists require nonempty string members')
        if any(not isinstance(getattr(self, name), str) for name in ('jurisdiction', 'material_update', 'records_request_text', 'records_custodian',
                'records_submission_url', 'records_route_note', 'caveat')):
            raise ValueError('Optional lead text must be strings')
        for score in (self.newsworthiness,self.obtainability):
            if type(score) is not int or not 1 <= score <= 10:
                raise ValueError('Scores must be integers between 1 and 10')
        if self.reviewer_verdict not in {'approved','rejected','needs_revision'}:
            raise ValueError('Invalid reviewer verdict')
        if self.acquisition_route not in {'public','records_request','other'}:
            raise ValueError('Invalid acquisition route')
        public_url(self.source_url)
        if self.records_submission_url:
            public_url(self.records_submission_url)
        required = (self.id,self.provisional_headline,self.story_potential,self.factual_hook,self.acquisition_details,self.next_action)
        if any(not isinstance(v,str) or not v.strip() for v in required) or not self.evidence_ids or not self.likely_records:
            raise ValueError('Lead is missing required content')


@dataclass
class Issue:
    issue_key: str
    local_date: date
    evidence: list[Evidence]
    leads: list[Lead]
    coverage_results: list[str]
    subject: str
    subject_lead_id: str | None = None
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self):
        if not isinstance(self.evidence, list) or any(not isinstance(e, Evidence) for e in self.evidence):
            raise ValueError('Issue evidence must be Evidence records')
        if not isinstance(self.leads, list) or any(not isinstance(l, Lead) for l in self.leads):
            raise ValueError('Issue leads must be Lead records')
        if not isinstance(self.coverage_results, list) or any(not isinstance(c, str) or not c.strip() for c in self.coverage_results):
            raise ValueError('Coverage must be nonempty text entries')
        if not isinstance(self.issue_key, str) or not self.issue_key.strip() or type(self.local_date) is not date:
            raise ValueError('Issue key and local date are required')
        if not isinstance(self.subject, str) or not self.subject.strip() or '\n' in self.subject or '\r' in self.subject:
            raise ValueError('Invalid subject')
        evidence_ids = {e.id for e in self.evidence}
        if len(evidence_ids) != len(self.evidence) or len({l.id for l in self.leads}) != len(self.leads):
            raise ValueError('Duplicate identifiers')
        for lead in self.leads:
            if lead.reviewer_verdict != 'approved' or not set(lead.evidence_ids) <= evidence_ids:
                raise ValueError('Issue requires approved leads and referenced evidence')
        if self.leads:
            selected = next((l for l in self.leads if l.id == self.subject_lead_id),None)
            if selected is None or self.subject != selected.provisional_headline:
                raise ValueError('Subject must exactly match the named final lead headline')
        elif self.subject_lead_id is not None or not self.coverage_results or self.subject != 'No qualifying document leads':
            raise ValueError('No-leads issue requires coverage results, no subject lead, and the fixed neutral subject')


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
