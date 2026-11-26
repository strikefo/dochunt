"""Strict TOML configuration; only secret names, never secret values."""
from dataclasses import dataclass, field, fields
from pathlib import Path
import re
import tomllib
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import get_origin, get_args

class ConfigError(ValueError):
    """A configuration problem with an operator-readable message (no secret values)."""


RUNTIMES = {'codex': 'OPENAI_API_KEY', 'claude': 'ANTHROPIC_API_KEY'}
DEFAULT_MODELS = {'codex': 'gpt-6.1-sol', 'claude': 'claude-opus-5-5'}


@dataclass
class Identity:
    id: str
    newsletter_name: str
    sender_name: str
    accent_color: str = '#152b44'


@dataclass
class Beat:
    name: str
    brief: str
    priorities: list[str] = field(default_factory=list)
    source_families: list[str] = field(default_factory=list)
    geography: str = 'United States'
    exclusions: str = ''
    records_law: str = 'the federal Freedom of Information Act (FOIA)'
    freshness_days: int = 3
    max_leads: int = 3
    extra_instructions: str = ''


@dataclass
class Agent:
    runtime: str
    model: str = ''
    reasoning_effort: str = 'high'
    max_budget_usd: float = 5.0
    timeout_minutes: int = 18


@dataclass
class Email:
    provider: str
    sender: str
    recipients: list[str]
    smtp_host: str = ''
    smtp_port: int = 587
    subscription_url: str = ''
    subscription_domains: list[str] = field(default_factory=list)


@dataclass
class Schedule:
    timezone: str
    enabled: bool = False
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    preferred_time: str = '08:00'
    window_hours: int = 12


@dataclass
class NewsroomConfig:
    identity: Identity
    beat: Beat
    agent: Agent
    email: Email
    schedule: Schedule

    @property
    def secret_names(self) -> dict[str, str]:
        names = {'ai': RUNTIMES[self.agent.runtime]}
        if self.email.provider == 'resend':
            names['email'] = 'RESEND_API_KEY'
        else:
            names.update(smtp_username='SMTP_USERNAME', smtp_password='SMTP_PASSWORD')
        if self.email.subscription_url:
            names['subscriptions'] = 'SUBSCRIPTION_API_TOKEN'
        return names

    @property
    def model(self) -> str:
        return self.agent.model or DEFAULT_MODELS[self.agent.runtime]


def _record(cls, data):
    if not isinstance(data, dict):
        raise ConfigError(f'{cls.__name__} must be a table')
    data = dict(data)
    for definition in fields(cls):
        if definition.name not in data:
            continue
        value = data[definition.name]
        kind = definition.type
        if kind is float and type(value) is int:
            value = data[definition.name] = float(value)
        if get_origin(kind) is list:
            item_kind = get_args(kind)[0]
            valid = isinstance(value, list) and all(type(item) is item_kind for item in value)
        else:
            valid = type(value) is kind
        if not valid:
            raise ConfigError(f'Invalid type for {cls.__name__}.{definition.name}')
    try:
        return cls(**data)
    except TypeError as exc:
        raise ConfigError(f'Invalid fields in {cls.__name__}') from exc


def load_config(path: Path) -> NewsroomConfig:
    try:
        with Path(path).open('rb') as stream:
            data = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError('Invalid TOML configuration') from exc
    return config_from_data(data)


SECTIONS = {'identity': Identity, 'beat': Beat, 'agent': Agent, 'email': Email, 'schedule': Schedule}


def _single_line(value):
    return isinstance(value, str) and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def config_from_data(data: dict) -> NewsroomConfig:
    unknown = set(data) - set(SECTIONS)
    if unknown:
        raise ConfigError(f'Unknown configuration section: {", ".join(sorted(unknown))}')
    missing = set(SECTIONS) - set(data)
    if missing:
        raise ConfigError(f'Missing configuration section: {", ".join(sorted(missing))}')
    c = NewsroomConfig(**{key: _record(cls, data[key]) for key, cls in SECTIONS.items()})
    for label, value in (('identity.id', c.identity.id), ('identity.newsletter_name', c.identity.newsletter_name),
                         ('identity.sender_name', c.identity.sender_name), ('beat.name', c.beat.name), ('beat.brief', c.beat.brief)):
        if not value.strip():
            raise ConfigError(f'{label} is empty')
    for label, value in (('identity.newsletter_name', c.identity.newsletter_name), ('identity.sender_name', c.identity.sender_name),
                         ('beat.name', c.beat.name), ('beat.geography', c.beat.geography), ('beat.records_law', c.beat.records_law)):
        if not _single_line(value):
            raise ConfigError(f'{label} must be a single line')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', c.identity.id):
        raise ConfigError('identity.id must use lowercase letters, digits, - or _')
    if not re.fullmatch(r'#[0-9a-fA-F]{6}', c.identity.accent_color):
        raise ConfigError('identity.accent_color must look like #152b44')
    if any(not s.strip() or not _single_line(s) for s in c.beat.priorities + c.beat.source_families):
        raise ConfigError('beat.priorities and beat.source_families need nonempty single-line entries')
    if not 1 <= c.beat.max_leads <= 5:
        raise ConfigError('beat.max_leads must be 1-5')
    if not 1 <= c.beat.freshness_days <= 30:
        raise ConfigError('beat.freshness_days must be 1-30')
    if c.agent.runtime not in RUNTIMES:
        raise ConfigError('agent.runtime must be codex or claude')
    if c.agent.model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,80}', c.agent.model):
        raise ConfigError('agent.model is not a valid model name')
    family = c.agent.model.lower()
    if (c.agent.runtime == 'claude' and family.startswith(('gpt', 'o1', 'o3', 'o4', 'codex'))) or \
            (c.agent.runtime == 'codex' and family.startswith('claude')):
        raise ConfigError(f'agent.model {c.agent.model} does not match agent.runtime {c.agent.runtime}; '
                          'leave agent.model empty to use the default')
    if c.agent.reasoning_effort not in {'low', 'medium', 'high', 'xhigh', 'max'}:
        raise ConfigError('agent.reasoning_effort must be low, medium, high, xhigh or max')
    if not 0.5 <= c.agent.max_budget_usd <= 50:
        raise ConfigError('agent.max_budget_usd must be 0.5-50')
    if not 5 <= c.agent.timeout_minutes <= 20:
        raise ConfigError('agent.timeout_minutes must be 5-20 so a run fits the 30-minute workflow')
    if c.email.provider not in {'resend', 'smtp'}:
        raise ConfigError('email.provider must be resend or smtp')
    addresses = [c.email.sender, *c.email.recipients]
    if not c.email.recipients or any(not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', a) for a in addresses):
        raise ConfigError('email.sender and email.recipients need valid addresses')
    if len(set(c.email.recipients)) != len(c.email.recipients):
        raise ConfigError('email.recipients has duplicates')
    if c.email.subscription_url:
        from urllib.parse import urlsplit
        u = urlsplit(c.email.subscription_url)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
            raise ConfigError('email.subscription_url must be an https origin such as https://subscriptions.example.org')
        if not c.email.subscription_domains or any(not re.fullmatch(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+', d) for d in c.email.subscription_domains):
            raise ConfigError('email.subscription_domains must list the lowercase domains subscribers may use, such as ["newsroom.example"]')
    elif c.email.subscription_domains:
        raise ConfigError('email.subscription_domains needs email.subscription_url')
    if c.email.provider == 'smtp' and (not c.email.smtp_host or not 1 <= c.email.smtp_port <= 65535):
        raise ConfigError('SMTP host and valid port required')
    try:
        ZoneInfo(c.schedule.timezone)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ConfigError('schedule.timezone must be an IANA timezone such as America/Chicago') from exc
    if not c.schedule.weekdays or any(not 0 <= d <= 6 for d in c.schedule.weekdays) or len(set(c.schedule.weekdays)) != len(c.schedule.weekdays):
        raise ConfigError('schedule.weekdays must be unique integers 0-6 (0 is Monday)')
    if not re.fullmatch(r'\d{2}:\d{2}', c.schedule.preferred_time):
        raise ConfigError('schedule.preferred_time must be HH:MM')
    if not 1 <= c.schedule.window_hours <= 18:
        raise ConfigError('schedule.window_hours must be 1-18')
    try:
        time.fromisoformat(c.schedule.preferred_time)
    except ValueError as exc:
        raise ConfigError('schedule.preferred_time is not a valid time') from exc
    return c
