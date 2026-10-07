"""Strict TOML configuration; only secret names, never secret values."""
from dataclasses import dataclass, field, fields
from pathlib import Path
import re
import tomllib
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import get_origin, get_args
from .models import Limits, public_url

@dataclass
class Identity:
    id: str
    newsletter_name: str
    sender_name: str
    accent_color: str = '#152b44'

@dataclass
class Editorial:
    coverage_brief: str
    exclusions: str = ''
    geography: str = ''
    max_final_leads: int = 8

@dataclass
class Source:
    url: str
    label: str = ''
    notes: str = ''

@dataclass
class AI:
    provider: str
    model: str
    reasoning_effort: str = 'medium'
    reviewer_model: str = ''

@dataclass
class Email:
    provider: str
    sender: str
    recipients: list[str]
    smtp_host: str = ''
    smtp_port: int = 587

@dataclass
class Schedule:
    timezone: str
    enabled: bool = False
    weekdays: list[int] = field(default_factory=lambda: [0,1,2,3,4])
    preferred_time: str = '08:00'

@dataclass
class Retrieval:
    firecrawl_enabled: bool = False
    firecrawl_max_calls: int = 8
    firecrawl_timeout_seconds: int = 30

@dataclass
class Hunt:
    playbook: str = ''
    allowed_acronyms: list[str] = field(default_factory=list)
    max_source_chars: int = 240000
    mode: str = 'watch'                  # 'watch': new documents on the watch list; 'search': open web search
    window_days: int = 14
    max_new_documents: int = 5
    watch: list[str] = field(default_factory=list)
    request_pages: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)   # feed items must mention one, if set
    seen_path: str = 'data/seen-documents.json'


@dataclass
class NewsroomConfig:
    identity: Identity
    editorial: Editorial
    ai: AI
    email: Email
    schedule: Schedule
    sources: list[Source] = field(default_factory=list)
    limits: Limits = field(default_factory=Limits)
    retrieval: Retrieval = field(default_factory=Retrieval)
    hunt: Hunt = field(default_factory=Hunt)

    @property
    def secret_names(self) -> dict[str,str]:
        names={'ai': {'openai':'OPENAI_API_KEY','anthropic':'ANTHROPIC_API_KEY'}[self.ai.provider]}
        if self.email.provider == 'resend': names['email']='RESEND_API_KEY'
        else: names.update(smtp_username='SMTP_USERNAME',smtp_password='SMTP_PASSWORD')
        if self.retrieval.firecrawl_enabled: names['retrieval']='FIRECRAWL_API_KEY'
        return names


def _record(cls, data):
    if not isinstance(data,dict): raise ValueError(f'{cls.__name__} must be a table')
    for definition in fields(cls):
        if definition.name not in data: continue
        value = data[definition.name]
        kind = definition.type
        if get_origin(kind) is list:
            item_kind = get_args(kind)[0]
            valid = isinstance(value, list) and all(type(item) is item_kind for item in value)
        else:
            valid = type(value) is kind
        if not valid: raise ValueError(f'Invalid type for {cls.__name__}.{definition.name}')
    try: return cls(**data)
    except TypeError as exc: raise ValueError(f'Invalid fields in {cls.__name__}') from exc


def load_config(path: Path) -> NewsroomConfig:
    try:
        with Path(path).open('rb') as stream: data=tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError('Invalid TOML configuration') from exc
    return config_from_data(data)


def config_from_data(data: dict) -> NewsroomConfig:
    allowed={'identity','editorial','ai','email','schedule','sources','limits','retrieval','hunt'}
    if set(data)-allowed: raise ValueError('Unknown configuration section')
    required=allowed-{'sources','limits','retrieval','hunt'}
    if not required <= data.keys(): raise ValueError('Missing required configuration section')
    if not isinstance(data.get('sources', []), list): raise ValueError('Sources must be a list of tables')
    c=NewsroomConfig(*[_record(cls,data[key]) for cls,key in [(Identity,'identity'),(Editorial,'editorial'),(AI,'ai'),(Email,'email'),(Schedule,'schedule')]],sources=[_record(Source,s) for s in data.get('sources',[])],limits=_record(Limits,data.get('limits',{})),retrieval=_record(Retrieval,data.get('retrieval',{})),hunt=_record(Hunt,data.get('hunt',{})))
    for value in (c.identity.id,c.identity.newsletter_name,c.identity.sender_name,c.editorial.coverage_brief,c.ai.model):
        if not isinstance(value,str) or not value.strip(): raise ValueError('Required text field is empty')
    if any(ord(char)<32 or ord(char)==127 for char in c.identity.sender_name): raise ValueError('Invalid sender name')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',c.identity.id): raise ValueError('Invalid newsroom identifier')
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',c.identity.accent_color): raise ValueError('Invalid accent color')
    if c.ai.provider not in {'openai','anthropic'}: raise ValueError('Unsupported AI provider')
    if c.ai.reasoning_effort not in {'low','medium','high','xhigh','max'}: raise ValueError('Unsupported reasoning effort')
    # Model capabilities are checked by each integration before paid research.
    if c.email.provider not in {'resend','smtp'}: raise ValueError('Unsupported email provider')
    addresses=[c.email.sender,*c.email.recipients] if isinstance(c.email.recipients,list) else []
    if not addresses or not c.email.recipients or any(not isinstance(a,str) or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',a) for a in addresses): raise ValueError('Invalid email addresses')
    if c.email.provider == 'smtp' and (not c.email.smtp_host or type(c.email.smtp_port) is not int or not 1 <= c.email.smtp_port <= 65535): raise ValueError('SMTP host and valid port required')
    try: ZoneInfo(c.schedule.timezone)
    except (ZoneInfoNotFoundError,TypeError,ValueError) as exc: raise ValueError('Explicit valid IANA timezone required') from exc
    if type(c.schedule.enabled) is not bool: raise ValueError('Schedule enabled must be boolean')
    if not isinstance(c.schedule.weekdays,list) or not c.schedule.weekdays or any(type(d) is not int or not 0 <= d <= 6 for d in c.schedule.weekdays) or len(set(c.schedule.weekdays)) != len(c.schedule.weekdays): raise ValueError('Weekdays must be unique integers 0–6')
    if not isinstance(c.schedule.preferred_time,str) or not re.fullmatch(r'\d{2}:\d{2}',c.schedule.preferred_time): raise ValueError('Preferred time must be HH:MM')
    try: time.fromisoformat(c.schedule.preferred_time)
    except ValueError as exc: raise ValueError('Invalid preferred time') from exc
    if type(c.editorial.max_final_leads) is not int or not 1 <= c.editorial.max_final_leads <= c.limits.max_final_leads: raise ValueError('Editorial final leads exceed configured limit')
    if not 1 <= c.retrieval.firecrawl_max_calls <= 20: raise ValueError('Firecrawl attempt cap must be 1–20')
    if not 5 <= c.retrieval.firecrawl_timeout_seconds <= 60: raise ValueError('Firecrawl timeout must be 5–60 seconds')
    for source in c.sources: public_url(source.url)
    if c.hunt.mode not in {'search','watch'}: raise ValueError('Hunt mode must be search or watch')
    if not 1 <= c.hunt.window_days <= 90: raise ValueError('Hunt window must be 1-90 days')
    if not 1 <= c.hunt.max_new_documents <= 20: raise ValueError('Hunt new-document cap must be 1-20')
    for url in c.hunt.watch + c.hunt.request_pages: public_url(url)
    if not 20000 <= c.hunt.max_source_chars <= 600000: raise ValueError('Hunt source allowance must be 20,000–600,000 characters')
    if any(not re.fullmatch(r'[A-Z][A-Z0-9.&]{1,9}',a) for a in c.hunt.allowed_acronyms): raise ValueError('Allowed acronyms must be short uppercase tokens')
    return c


class ModelConfigurationError(ValueError):
    """Safe operator guidance containing only application-owned model names."""


def validate_model_configuration(config: NewsroomConfig) -> None:
    """Check operator model choices without keys, provider instances or network.

    Keep generic configuration loading usable by injected fixture adapters;
    operator readiness and setup explicitly opt into supported live models.
    """
    from .providers.openai import OpenAIProvider
    from .providers.anthropic import AnthropicProvider
    providers={'openai': OpenAIProvider, 'anthropic': AnthropicProvider}
    supported=providers[config.ai.provider].supported_models
    for field in ('model','reviewer_model'):
        value=getattr(config.ai,field)
        if field=='reviewer_model' and value=='': continue
        if value not in supported:
            suffix=' Leave ai.reviewer_model empty to use the research model.' if field=='reviewer_model' else ''
            raise ModelConfigurationError(f'Unsupported ai.{field}. Choose a model supported by the selected provider: {", ".join(supported)}.{suffix}')
