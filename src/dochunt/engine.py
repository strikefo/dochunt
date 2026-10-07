"""Evidence engine: whole-source analysis, record-level access gates, one independent review.

Two model calls per supplied-source run (analysis, review). The model proposes; the
application decides what is supported:

* every factual statement and every access route carries a literal quote that must occur
  in the named source document;
* every record the reporting test depends on must carry its own access route (a public
  download or portal, or a custodian request route) supported by a quote;
* at least one essential record must be an underlying record, not the seed document;
* the headline must be short and free of unexplained acronyms;
* the independent reviewer may only approve or reject. Nothing is rewritten after review,
  so no correction can hide an unresolved blocker.

Leads that fail access or presentation gates are held with a reason for the operator,
never placed in the email.
"""
from dataclasses import dataclass, field, asdict
import hashlib
import json
import re
import tomllib
import unicodedata
from importlib.resources import files
from pathlib import Path

from .editorial import validate_request_placeholders
from .models import Evidence, Lead

ENGINE_ID = 'evidence-v1'
ROUTES = ('public_download', 'records_request', 'unknown')
KINDS = ('underlying', 'seed')
MAX_HEADLINE_WORDS = 14
MAX_REQUEST_CHARS = 1800
MAX_LEADS = 3
HOOK_FACTS = 3
REQUEST_PAGE_CHARS = 12000
# The only access problems that may be shown as "check the request route first".
UNCONFIRMED_ROUTE = ('custodian is not named where the route is given', 'no passage shows this as a place to submit requests')
STAGE_OUTPUT_CAPS = {'analysis': 12000, 'review': 4000}
DEFAULT_ALLOWED_ACRONYMS = ('US', 'U.S.', 'FOIA', 'FOIL')


class EngineError(ValueError):
    """Malformed model output or unusable input; never a silent success."""


# ---------------------------------------------------------------- sources

@dataclass
class SourceDoc:
    id: str
    url: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)
    retrieved_at: str = ''
    format: str = ''
    note: str = ''
    published: str = ''                 # ISO date, set by the watcher with literal evidence
    published_evidence: str = ''
    published_approximate: bool = False
    listed_url: str = ''

    @property
    def sha256(self):
        return hashlib.sha256(self.text.encode()).hexdigest()


def source_from_receipt(receipt, source_id):
    """Accept an eval receipt ({text}) or a production receipt ({sections})."""
    text = receipt.get('text')
    if text is None:
        text = receipt.get('content') or '\n\n'.join(
            s.get('text', '') for s in receipt.get('sections', []) if isinstance(s, dict))
    note = receipt.get('acquisition_note') or (receipt.get('coverage') if isinstance(receipt.get('coverage'), str) else '')
    if receipt.get('truncated_at_acquisition') or len(text) >= 120000:
        note = (note + '; ' if note else '') + 'text was cut off at acquisition; later pages were not read'
    return SourceDoc(source_id, receipt['url'], receipt.get('title') or receipt['url'], text,
                     list(receipt.get('links') or []), receipt.get('retrieved_at') or '',
                     receipt.get('format') or 'text', note)


def pack_sources(docs, max_chars):
    """Whole documents in order. A document that does not fit keeps its beginning and
    end (where agency responses and qualifications usually sit) with an explicit
    omission marker; it is listed as partially read, never silently clipped."""
    packed, partial, remaining = [], [], max_chars
    for doc in docs:
        text = doc.text
        if len(text) > remaining:
            if remaining < 4000:
                partial.append(doc.id)
                packed.append({'id': doc.id, 'url': doc.url, 'title': doc.title, 'note': 'not included: context allowance exhausted', 'text': ''})
                continue
            half = (remaining - 200) // 2
            omitted = len(text) - 2 * half
            text = text[:half] + f'\n\n[... {omitted} characters omitted by DocHunt; not read ...]\n\n' + text[-half:]
            partial.append(doc.id)
        remaining -= len(text)
        entry = {'id': doc.id, 'url': doc.url, 'title': doc.title, 'retrieved_at': doc.retrieved_at,
                 'note': doc.note, 'links': doc.links[:60], 'text': text}
        if doc.published:
            entry['new_document_published'] = doc.published + (' (approximate)' if doc.published_approximate else '')
        packed.append(entry)
    return packed, partial


# ---------------------------------------------------------------- playbooks

def load_playbook(path):
    """Editable newsroom guidance: record families, where they live, reasons to reject.
    Contains no stories and promises no lead. URLs are starting points, not verified access."""
    if not path:
        return None
    with Path(path).open('rb') as stream:
        data = tomllib.load(stream)
    if not isinstance(data.get('name'), str) or not isinstance(data.get('record_families'), list):
        raise ValueError('Playbook requires a name and record_families')
    for family in data['record_families']:
        if not isinstance(family, dict) or not isinstance(family.get('name'), str):
            raise ValueError('Each record family requires a name')
    return data


def playbook_urls(playbook):
    if not playbook:
        return []
    return [s['url'] for f in playbook['record_families'] for s in f.get('sources', []) if isinstance(s, dict) and s.get('url')]


# ---------------------------------------------------------------- literal support

_TRANSLATE = str.maketrans({'‘': "'", '’': "'", '“': '"', '”': '"', '–': '-',
                            '—': '-', '‑': '-', '‐': '-', ' ': ' ', '­': ''})


def _norm(text):
    text = unicodedata.normalize('NFKC', text).translate(_TRANSLATE)
    return ' '.join(text.split())


def _numbers(text):
    """Numeric values, so 43,159 matches 43,159.00 and 2 matches 02."""
    values = []
    for raw in re.findall(r'\d[\d,]*(?:\.\d+)?', _norm(text or '')):
        try:
            values.append(float(raw.replace(',', '')))
        except ValueError:
            continue
    return values


_PAGE_BREAK = re.compile(r'\bPage \d+\b')


def literal_span(quote, doc):
    """The source text a quote copies, or None. Surrounding quotation marks are ignored,
    and one PDF running header (a 'Page N' line whose other text repeats elsewhere in the
    document) interrupting the quoted sentence is tolerated; the returned span is the
    source's own text, header included, so evidence stays literal."""
    q = _norm(quote or '').strip(' "\'')
    if len(q) < 12:
        return None
    text = _norm(doc.text)
    if q in text:
        return q
    words = q.split(' ')
    for k in range(3, len(words) - 1):
        head, tail = ' '.join(words[:k]), ' '.join(words[k:])
        if len(head) < 20 or len(tail) < 8:
            continue
        start = text.find(head + ' ')
        while start >= 0:
            end = text.find(tail, start + len(head))
            gap = text[start + len(head):end] if end >= 0 else ''
            header = _PAGE_BREAK.sub(' ', gap).strip(' .')
            # Only a running header (text repeated elsewhere in the document) may be
            # skipped; content that appears once could carry a dropped qualification.
            if end >= 0 and len(gap) <= 220 and _PAGE_BREAK.search(gap) and (not header or text.count(header) >= 2):
                return text[start:end + len(tail)]
            start = text.find(head + ' ', start + 1)
    return None


def quote_supported(quote, doc):
    return literal_span(quote, doc) is not None


def _sentence_around(text, index, length):
    """The sentence containing a match plus the sentence before it (where pages usually
    say how to submit), bounded to about 300 characters each way."""
    floor = max(0, index - 300)
    previous = text.rfind('. ', floor, index)
    if previous > floor:
        previous = text.rfind('. ', floor, previous)
    start = previous + 2 if previous >= floor else floor
    stop = text.find('. ', index + length, index + length + 300)
    return text[start:(stop + 1) if stop >= 0 else min(len(text), index + length + 300)].strip()


_RECORDS_REQUEST = re.compile(r'\b(?:FOIL|FOIA|public records?|records? requests?|disclosure requests?|open records|CPRA|PDRs?)\b', re.I)


def records_request_sentences(text):
    """Sentences about records requests. For a linked route, one of them must say how to
    submit, not merely use "request" somewhere else."""
    return [s for s in re.split(r'(?<=[.!?])\s+', _norm(text)) if _RECORDS_REQUEST.search(s)]


def is_submission_text(text):
    """Says where to submit records requests: submission wording plus records-request
    wording, and not a hotline or complaint channel."""
    return bool(_SUBMIT.search(text) and _RECORDS_REQUEST.search(text) and not _NOT_RECORDS.search(text))


def submission_passage(location, docs, preferred):
    """A passage showing the location as a place to submit requests: every occurrence of
    the location on the cited page, then on other supplied pages, is checked."""
    tokens = _addresses(location) or [_norm(location)]
    for doc in [preferred] + [d for d in docs if d is not preferred]:
        if doc is None:
            continue
        text = _norm(doc.text)
        lower, needle = text.casefold(), tokens[0].casefold()
        index = lower.find(needle)
        while index >= 0:
            window = _sentence_around(text, index, len(needle))
            if all(t.casefold() in window.casefold() for t in tokens) and is_submission_text(window):
                return doc, window
            index = lower.find(needle, index + 1)
    return None, ''


def own_sentence(text, phrase):
    """Just the sentence containing a phrase (no neighbouring sentence)."""
    index = text.casefold().find(_norm(phrase).casefold())
    if index < 0:
        return ''
    start = text.rfind('. ', 0, index) + 2 if text.rfind('. ', 0, index) >= 0 else 0
    stop = text.find('. ', index)
    return text[start:(stop + 1) if stop >= 0 else len(text)]


def locate(location, docs, preferred):
    """Find a route location on the cited page first, then on any other supplied page.
    Returns (doc, sentence) with the sentence literally containing every address in the
    location (or the whole location when it has no address), or (None, '')."""
    tokens = _addresses(location) or [_norm(location)]
    for doc in [preferred] + [d for d in docs if d is not preferred]:
        if doc is None:
            continue
        text = _norm(doc.text)
        lower = text.casefold()
        hits = [lower.find(t.casefold()) for t in tokens]
        if hits and all(h >= 0 for h in hits):
            return doc, _sentence_around(text, hits[0], len(tokens[0]))
    return None, ''


ADDRESS = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+|https?://[^\s)\]>"]+|\bwww\.[\w-]+\.[\w./-]+')


def _addresses(text):
    return [t.rstrip('.,;') for t in ADDRESS.findall(text or '')]


def location_basis(location, quote, doc):
    """Where a route's location (URL, email, portal or office name) is attested: in the
    quoted passage, as the cited page itself, or among that page's own links. Returns ''
    when unsupported. Every address inside a longer location must be attested. No
    same-host, acronym or organisational inference."""
    loc = _norm(location or '')
    if not loc:
        return ''
    q = _norm(quote or '').casefold()
    if loc.casefold() in q:
        return 'quote'
    if loc == _norm(doc.url):
        return 'source_url'
    if loc in doc.links:
        return 'links'
    tokens = _addresses(location)
    if not tokens:
        return ''
    bases = set()
    for token in tokens:
        if token.casefold() in q:
            bases.add('quote')
        elif token in doc.links:
            bases.add('links')
        else:
            return ''
    return 'links' if 'links' in bases else 'quote'


_STOP = {'records', 'record', 'data', 'public', 'with', 'from', 'that', 'this', 'their', 'each', 'those',
         'these', 'since', 'existing', 'report', 'reports', 'other', 'about', 'related', 'including', 'available',
         'information', 'document', 'documents', 'files', 'file', 'list', 'copies', 'three', 'such', 'which', 'were'}


def _content_words(text):
    words = re.findall(r'[a-z][a-z-]{3,}', _norm(text).casefold())
    return {w[:-1] if w.endswith('s') else w for w in words} - _STOP


def link_digest(location, page_url, page_sha256):
    """Binds a linked route to the page and page text it was checked against. Unkeyed:
    it detects edits to an app-owned issue file, not a deliberate forgery."""
    return hashlib.sha256('\n'.join((location, page_url, page_sha256)).encode()).hexdigest()


_GENERIC_NAME = {'office', 'department', 'division', 'national', 'records', 'record', 'public', 'state', 'city',
                 'county', 'village', 'town', 'the', 'and', 'for', 'foia', 'foil', 'coordinator', 'officer', 'center',
                 'service', 'access', 'request', 'requests', 'information', 'freedom', 'act', 'law', 'u.s.', 'united',
                 'states', 'inspector', 'general', 'bureau', 'agency', 'administration', 'commission', 'clerk',
                 'custodian', 'unit', 'program', 'services', 'government', 'federal', 'oig'}
_ANY_DEPARTMENT = re.compile(r'\b(?:to|for) any (?:city |county |state |village )?(?:department|agency|office|component)s?\b(?!\s+records?\b)'
                             r'|\ball (?:city |county )?departments\b', re.I)
# A linked request route must itself look like one: a FOIA/records/request page or a
# known request-portal vendor, not any link on the page.
_ROUTE_URL = re.compile(r'foia|foil|privacy-?act|records?|request|portal|submit|form|pal\.|openrecords|casepoint|'
                        r'secureocp|govqa|nextrequest|mycusthelp|justfoia|foiaxpress', re.I)
_NOT_RECORDS = re.compile(r'\b(?:hotline|file (?:a |your )?complaint|complaint form|report (?:fraud|waste|abuse))\b', re.I)
_SUBMIT = re.compile(r'\b(?:submit\w*|send|sent|file|filing|accept\w*|process(?:es|ed)?|make (?:a |your )?request|(?:may|can) request|'
                     r'portal|request form|online form|records access officer)\b', re.I)


def request_problems(text):
    """Existing-records request language: asks for records, no unresolved identifying slots."""
    problems = []
    try:
        validate_request_placeholders(text)
    except ValueError as exc:
        problems.append(str(exc))
    if not re.search(r'\brecords?\b', text, re.I) or not re.search(
            r'\b(?:request\w*|provide|produce|cop(?:y|ies)|seek\w*)\b', text, re.I):
        problems.append('request language must ask for existing records')
    return problems


# Separate bodies only; divisions and offices are usually parts of the custodian.
_ORG = re.compile(r"(?:[A-Z][\w&.'-]*\s+){0,6}(?:Department|Agency|Bureau|Commission|Board|Authority)"
                  r"(?:\s+of(?:\s+(?:the\s+)?[A-Z][\w&.'-]*)+)?")


def _distinguishing(name):
    return {w.casefold() for w in re.findall(r"[A-Za-z][\w.'-]+", name or '')} - _GENERIC_NAME - {'los', 'new', 'san', 'of'}


def names_other_office(text, custodian):
    """The passage names an office whose distinguishing words are not the custodian's
    (for example the Fire Department on a Public Health page). Then the rest of the page
    cannot vouch that this address belongs to the custodian."""
    own = _distinguishing(custodian)
    for org in _ORG.findall(text):
        words = {w for w in _distinguishing(org) if len(w) >= 3}
        if words and not words <= own:
            return True
    return False


def custodian_named(custodian, text):
    """The custodian's agency-distinguishing words (not generic titles such as "Inspector
    General" or "Records Office") appear in the text: at least one, and at least half of
    them. Or the text accepts requests for any department. A lexical screen, not proof."""
    if _ANY_DEPARTMENT.search(text):
        return True
    words = [w for w in {w.casefold() for w in re.findall(r"[A-Za-z][\w.'-]+", custodian or '')} - _GENERIC_NAME if len(w) >= 3]
    lower = text.casefold()
    found = sum(1 for w in words if re.search(r'\b' + re.escape(w) + r'\b', lower))
    return bool(words) and found >= 1 and found * 2 >= len(words)


_ACCESS_WORDS = re.compile(r'\b(?:download\w*|dataset\w*|data|portal|online|database|spreadsheet|search)\b', re.I)


def route_reaches_record(record, doc):
    """A public download must be described by its own passage: the quote must share at
    least two content words with the record's description. A request route must name the
    record's custodian on the cited page. Lexical screens, not semantic proof; the
    independent reviewer still judges whether the route reaches the record."""
    if record.get('route') == 'public_download':
        quote = _norm(record.get('quote', '')).casefold()
        names_access = (_norm(record.get('location', '')).casefold() in quote
                        or re.search(r'\b(?:download|dataset|data|portal|online|database|spreadsheet|search)\b', quote))
        return bool(names_access) and len(_content_words(record.get('description', '')) & _content_words(quote)) >= 2
    custodian = _norm(record.get('custodian', '')).casefold()
    return len(custodian) >= 3 and custodian in _norm(doc.text).casefold()


# ---------------------------------------------------------------- gates

_ACRONYM = re.compile(r'\b(?:[A-Z]\.){2,}|\b[A-Z][A-Z0-9&]{1,}\b')


def presentation_problems(headline, allowed=DEFAULT_ALLOWED_ACRONYMS):
    problems = []
    words = headline.split()
    if not words:
        return ['missing headline']
    if len(words) > MAX_HEADLINE_WORDS:
        problems.append(f'headline has {len(words)} words; limit is {MAX_HEADLINE_WORDS}')
    allowed = {a.casefold() for a in allowed}
    acronyms = sorted({a for a in _ACRONYM.findall(headline) if a.casefold() not in allowed})
    if acronyms:
        problems.append('headline uses unexplained acronyms: ' + ', '.join(acronyms))
    return problems


@dataclass
class Checked:
    lead: dict
    status: str              # ready | held_access | held_seed_only | held_presentation | unsupported
    problems: list[str]
    evidence: list[dict]     # [{source, quote}] that passed literal checks


def check_lead(lead, docs, *, allowed_acronyms=DEFAULT_ALLOWED_ACRONYMS, geography='', fresh_ids=None):
    """Deterministic, application-owned checks of one analysed lead."""
    by_id = {d.id: d for d in docs}
    problems, evidence = [], []

    def supported(item, what):
        doc = by_id.get(item.get('source', ''))
        span = literal_span(item.get('quote', ''), doc) if doc is not None else None
        if span is None:
            problems.append(f'{what} lacks a literal quote from a supplied source: {item.get("text", "")[:90]}')
            return False
        # The watcher's publication date (from the feed or the document's labelled date)
        # is part of what is known about a new document.
        source_numbers = set(_numbers(doc.text + ' ' + doc.published.replace('-', ' ')))  # year, month, day only
        missing = [n for n in _numbers(item.get('text', '')) if n not in source_numbers]
        if missing:
            shown = ', '.join(f'{n:g}' for n in missing)
            problems.append(f'{what} states numbers not in its source ({shown}): {item.get("text", "")[:90]}')
            return False
        evidence.append({'source': doc.id, 'quote': span})
        item['quote'] = span  # the reviewer sees the source's own text, not the model's copy
        return True

    established = [f for f in lead.get('established', []) if supported(f, 'fact')]
    qualifications = [f for f in lead.get('qualifications', []) if supported(f, 'qualification')]
    if not established or len(established) < len(lead.get('established', [])):
        return Checked(lead, 'unsupported', problems, evidence)
    if len(qualifications) < len(lead.get('qualifications', [])):
        # An unsupported qualification means the lead's framing is unreliable; do not
        # drop it and keep the rest.
        return Checked(lead, 'unsupported', problems, evidence)

    if fresh_ids is not None and not any(f.get('source') in fresh_ids for f in established):
        # Watch mode: a lead must be built on what a newly published document says.
        return Checked(lead, 'held_stale', problems + ['no established fact comes from a newly published document'], evidence)

    essential = [r for r in lead.get('records', []) if r.get('essential')]
    if not essential:
        return Checked(lead, 'held_seed_only', problems + ['no essential further record'], evidence)
    if not any(r.get('kind') == 'underlying' for r in essential):
        return Checked(lead, 'held_seed_only', problems + ['every essential record is the seed document itself'], evidence)

    access_problems = []
    for record in essential:
        route = record.get('route')
        doc = by_id.get(record.get('source', ''))
        name = record.get('description', '')[:90]
        if route not in ('public_download', 'records_request'):
            access_problems.append(f'no supported access route for essential record: {name}')
            continue
        if not (record.get('location') or '').strip():
            access_problems.append(f'no location given for this route: {name}')
            continue
        span = literal_span(record.get('quote', ''), doc) if doc is not None else None
        if span is None:
            access_problems.append(f'access route lacks a literal quote: {name}')
            continue
        basis = location_basis(record.get('location', ''), span, doc)
        route_doc, route_text = doc, span
        if not basis:
            # The location may sit in the next sentence or on a companion page. Use the
            # sentence that literally contains it as the route's evidence.
            route_doc, route_text = locate(record.get('location', ''), docs, doc)
            if route_doc is None:
                access_problems.append(f'access location is not in any supplied source: {name}')
                continue
            basis = 'quote'
        if record.get('kind') == 'underlying':
            if _norm(record.get('location', '')) in {_norm(d.url) for d in docs}:
                access_problems.append(f'route points to a document already supplied, not to further records: {name}')
                continue
            if route == 'public_download':
                # A name-only location found elsewhere must be described by its own sentence;
                # the model's quote cannot supply the overlap.
                if (route_doc is not doc or route_text != span) and not _addresses(record.get('location', '')):
                    # A name found elsewhere: its own sentence, minus the name itself, must
                    # say it is a download/data/portal and describe this record.
                    sentence = own_sentence(_norm(route_doc.text), record.get('location', ''))
                    rest = re.sub(re.escape(_norm(record.get('location', ''))), ' ', sentence, flags=re.I)
                    own = _content_words(record.get('location', ''))
                    reaches = bool(_ACCESS_WORDS.search(rest)) and len(
                        (_content_words(record.get('description', '')) - own) & (_content_words(rest) - own)) >= 2
                else:
                    reaches = route_reaches_record(dict(record, quote=span + ' ' + route_text), doc)
                if not reaches:
                    access_problems.append(f'route passage does not describe this record: {name}')
                    continue
            if route == 'records_request':
                # Judged sentence by sentence for a linked route: one passing sentence on the
                # page suffices, and an unrelated "hotline" elsewhere does not disqualify it.
                submits = is_submission_text(span + ' ' + route_text) or (
                    basis == 'links' and _ROUTE_URL.search(record.get('location', ''))
                    and any(is_submission_text(x) for x in records_request_sentences(doc.text)))
                if not submits:
                    # The quoted sentence may be a "contact us with questions" line while the
                    # same address is given elsewhere as the submission route.
                    quoted = span + ' ' + route_text
                    route_doc, route_text = submission_passage(record.get('location', ''), docs, doc)
                    if route_doc is None:
                        if _NOT_RECORDS.search(quoted):
                            # A wrong channel, not an unconfirmed route: never "check the route".
                            access_problems.append(f'address is a complaint or hotline channel, not a records-request route: {name}')
                        else:
                            access_problems.append(f'no passage shows this as a place to submit requests '
                                                   f'(a contact for questions is not a submission route): {name}')
                        continue
                    basis = 'quote'
                # The custodian must be named with the address, or by its distinguishing
                # words on the address's own page; another page cannot vouch for it.
                # Wherever the address itself appears in text, its own passage must not
                # assign it to another body (a linked route included).
                _, address_text = locate(record.get('location', ''), [route_doc], route_doc)
                if names_other_office(route_text + ' ' + address_text, record.get('custodian', '')):
                    # Affirmatively another body's route: never "check the route".
                    access_problems.append(f'address is assigned to another office: {name}')
                    continue
                if not (custodian_named(record.get('custodian', ''), route_text)
                        or custodian_named(record.get('custodian', ''), _norm(route_doc.text))):
                    access_problems.append(f'custodian is not named where the route is given: {name}')
                    continue
        record['location_basis'] = basis
        record['route_source'], record['route_quote'] = route_doc.id, route_text
        if basis == 'links':
            record['linked_from'] = doc.url
            record['link_digest'] = link_digest(record['location'], doc.url, doc.sha256)
        evidence.append({'source': doc.id, 'quote': span})
        if record['route_quote'] != span or route_doc is not doc:
            evidence.append({'source': route_doc.id, 'quote': record['route_quote']})
    # Addresses in free text must also come from the sources, so prose cannot carry an
    # invented route past the location check.
    attested = ' '.join(_norm(d.text).casefold() + ' ' + ' '.join(d.links).casefold() + ' ' + d.url.casefold() for d in docs)
    prose = ' '.join([lead.get('headline', ''), lead.get('hypothesis', ''), lead.get('already_answered', ''),
                      lead.get('request_text') or '']
                     + [f.get('text', '') for f in lead.get('established', []) + lead.get('qualifications', [])]
                     + [r.get(k, '') for r in lead.get('records', []) for k in ('description', 'could_reveal', 'how', 'location')])
    for token in _addresses(prose):
        if token.casefold() not in attested:
            access_problems.append(f'address not found in any source: {token}')
    if any(r.get('route') == 'records_request' for r in essential):
        text = (lead.get('request_text') or '').strip()
        if len(text) < 100:
            access_problems.append('a records request is needed but no request language was drafted')
        elif len(text) > MAX_REQUEST_CHARS:
            access_problems.append(f'request language exceeds {MAX_REQUEST_CHARS} characters; narrow the request')
        else:
            access_problems.extend(request_problems(text))
    if access_problems:
        # Presentation and grounding apply to every lead that could be shown.
        shown = presentation_problems(lead.get('headline', ''), allowed_acronyms)
        cited = [e['quote'] for e in evidence] + [by_id[e['source']].title for e in evidence]
        shown += headline_grounding(lead.get('headline', ''), cited, geography)
        if shown:
            return Checked(lead, 'held_presentation', problems + access_problems + shown, evidence)
        return Checked(lead, 'held_access', problems + access_problems, evidence)

    shown = presentation_problems(lead.get('headline', ''), allowed_acronyms)
    cited = [e['quote'] for e in evidence] + [by_id[e['source']].title for e in evidence]
    shown += headline_grounding(lead.get('headline', ''), cited, geography)
    if shown:
        return Checked(lead, 'held_presentation', problems + shown, evidence)
    return Checked(lead, 'ready', problems, evidence)


_QUESTION_WORDS = {'could', 'would', 'will', 'can', 'does', 'do', 'did', 'is', 'are', 'was', 'were', 'has', 'have',
                   'had', 'should', 'how', 'why', 'what', 'when', 'where', 'who', 'whose', 'which', 'the', 'a', 'an'}


def _lexical(text):
    text = re.sub(r"[’']s\b", '', _norm(text))
    return ' '.join(re.sub(r'(?<=\w)-(?=\w)', ' ', text).casefold().split())


def headline_grounding(headline, cited, geography=''):
    """A headline may not introduce a name or number its lead's evidence does not contain.
    Names count if they appear in the cited quotes, the cited sources' titles, or the
    newsroom's configured geography. Possessives and opening question words are ignored."""
    problems = []
    pool = _lexical(' '.join(cited) + ' ' + (geography or ''))
    numbers = set(_numbers(' '.join(cited)))
    missing_numbers = [n for n in _numbers(headline) if n not in numbers]
    if missing_numbers:
        problems.append('headline number is not in the cited evidence: ' + ', '.join(f'{n:g}' for n in missing_numbers))
    for name in re.findall(r"\b[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*)*", _norm(headline)):
        words = name.split()
        while words and words[0].casefold() in _QUESTION_WORDS:
            words = words[1:]
        if words and not re.search(r'\b' + re.escape(_lexical(' '.join(words))) + r'\b', pool):
            problems.append(f'headline name is not in the cited evidence: {" ".join(words)}')
    return problems


def hook_text(established):
    """The facts shown in the email; the headline must be grounded in these."""
    return ' '.join(f['text'].strip() for f in established[:HOOK_FACTS])


# ---------------------------------------------------------------- model stages

def _prompt(name):
    return files('dochunt').joinpath('prompts', name + '.md').read_text(encoding='utf-8')


def _json(text):
    text = (text or '').strip()
    fenced = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', text, re.S)  # tolerate a fenced reply
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EngineError('Model output is not JSON') from exc
    if not isinstance(data, dict):
        raise EngineError('Model output must be an object')
    return data


def _validate_analysis(data):
    leads = data.get('leads')
    if not isinstance(leads, list) or not isinstance(data.get('no_lead_reason', ''), str):
        raise EngineError('Analysis requires leads and no_lead_reason')
    for lead in leads:
        if not isinstance(lead, dict) or not isinstance(lead.get('headline'), str):
            raise EngineError('Each lead requires a headline')
        for key in ('established', 'qualifications', 'records'):
            if not isinstance(lead.get(key), list) or any(not isinstance(i, dict) for i in lead[key]):
                raise EngineError(f'Lead {key} must be a list of objects')
        for record in lead['records']:
            if record.get('route') not in ROUTES or record.get('kind') not in KINDS or type(record.get('essential')) is not bool:
                raise EngineError('Record requires route, kind and essential flag')
            if any(not isinstance(record.get(k, ''), str) for k in ('description', 'location', 'how', 'quote', 'source', 'custodian')):
                raise EngineError('Record fields must be strings')
        if type(lead.get('newsworthiness', 5)) is not int or not 1 <= lead.get('newsworthiness', 5) <= 10:
            raise EngineError('Newsworthiness must be an integer from 1 to 10')
    return leads[:MAX_LEADS]


def _validate_review(data, ids):
    verdicts = data.get('verdicts')
    if not isinstance(verdicts, list):
        raise EngineError('Review requires verdicts')
    out = {}
    for v in verdicts:
        if not isinstance(v, dict) or v.get('lead') not in ids or v.get('verdict') not in ('approve', 'reject'):
            raise EngineError('Invalid review verdict')
        if not isinstance(v.get('problems'), list) or any(not isinstance(p, str) for p in v['problems']):
            raise EngineError('Review problems must be strings')
        out[v['lead']] = v
    if set(out) != set(ids):
        raise EngineError('Review must judge every lead exactly once')
    return out


@dataclass
class EngineResult:
    approved: list[dict]
    held: list[dict]
    no_lead_reason: str
    partial_sources: list[str]
    calls: list[dict]
    analysed: list[str] = field(default_factory=list)   # watch mode: URLs of new documents analysed


def _call(provider, stage, payload, rules, sink=None):
    before = asdict(provider.budget.snapshot())
    prompt = 'UNTRUSTED NEWSROOM AND SOURCE DATA JSON:\n' + json.dumps(payload, ensure_ascii=False)
    if not getattr(provider, 'structured_outputs', False):
        # Providers without native structured output get the schema in their instructions.
        from .providers.schemas import output_schema
        rules = (rules + '\n\nRespond with one JSON object only (no prose, no code fences) matching this JSON schema:\n'
                 + json.dumps(output_schema('evidence_' + stage, provider.budget.limits)))
    prior_cap = provider.stage_output_cap
    provider.stage_output_cap = STAGE_OUTPUT_CAPS[stage]  # includes reasoning tokens
    try:
        text, _, _ = provider._request(prompt, rules, 'research_requests' if stage == 'analysis' else 'review_requests',
                                       output_kind='evidence_' + stage)
    finally:
        provider.stage_output_cap = prior_cap
    after = asdict(provider.budget.snapshot())
    meta = {'stage': stage, 'model': provider.model, 'usage': {k: after[k] - before[k] for k in after}}
    if sink is not None:
        # Paid output is persisted before anything that can fail validates it.
        sink(stage, text, meta)
    return text, meta


def analyse(provider, *, brief, geography, docs, playbook=None, allowed_acronyms=DEFAULT_ALLOWED_ACRONYMS,
            max_source_chars=240000, reviewer=None, sink=None, fresh_ids=None, today=None):
    """Run analysis and independent review over supplied documents."""
    if not docs:
        raise EngineError('No readable source documents')
    packed, partial = pack_sources(docs, max_source_chars)
    context = {'brief': brief, 'geography': geography, 'today': today or '', 'sources': packed,
               'partially_read_sources': partial,
               'playbook': playbook, 'allowed_acronyms': list(allowed_acronyms)}
    calls = []
    text, meta = _call(provider, 'analysis', context, _prompt('evidence-analysis'), sink)
    calls.append(meta)
    data = _json(text)
    leads = _validate_analysis(data)
    checked = [check_lead(lead, docs, allowed_acronyms=allowed_acronyms, geography=geography, fresh_ids=fresh_ids)
               for lead in leads]
    # Leads whose facts, freshness and presentation pass but whose request route the
    # lexical access checks could not confirm still go to review, flagged as such. If
    # approved they are shown separately, with what the reporter must confirm.
    # A missing route (no route or no location proposed) is not "unconfirmed": those leads
    # stay held, as does anything failing facts, freshness or presentation.
    def unconfirmed_only(c):
        # Allow-list: only a specific proposed route the lexical checks could not confirm.
        return c.status == 'held_access' and bool(c.problems) and all(p.startswith(UNCONFIRMED_ROUTE) for p in c.problems)
    reviewable = [c for c in checked if c.status == 'ready' or unconfirmed_only(c)]
    held = [{'headline': c.lead.get('headline', ''), 'status': c.status, 'problems': c.problems}
            for c in checked if c not in reviewable]
    approved = []
    if reviewable:
        ids = [f'L{i}' for i in range(1, len(reviewable) + 1)]
        review_context = {'brief': brief, 'geography': geography, 'sources': packed, 'partially_read_sources': partial,
                          'leads': [{'lead': i, 'route_unverified': c.status == 'held_access', **c.lead}
                                    for i, c in zip(ids, reviewable)]}
        text, meta = _call(reviewer or provider, 'review', review_context, _prompt('evidence-review'), sink)
        calls.append(meta)
        verdicts = _validate_review(_json(text), ids)
        for i, c in zip(ids, reviewable):
            v = verdicts[i]
            if v['verdict'] == 'approve' and not v['problems']:
                tier = 'ready' if c.status == 'ready' else 'verify_route'
                approved.append({**c.lead, '_evidence': c.evidence, '_tier': tier,
                                 '_route_notes': [] if tier == 'ready' else route_notes(c.problems)})
            else:
                held.append({'headline': c.lead.get('headline', ''), 'status': 'rejected_by_review', 'problems': v['problems'] or ['rejected']})
    approved.sort(key=lambda a: a['_tier'] != 'ready')  # confirmed routes first
    return EngineResult(approved, held, data.get('no_lead_reason', ''), partial, calls)


def route_notes(problems):
    """Plain-language list of what the reporter must confirm before filing."""
    notes = []
    for p in problems:
        what, _, record = p.partition(': ')
        notes.append(f'{record.strip() or "Request"}: {what.strip()}.')
    return notes[:4]


# ---------------------------------------------------------------- email records

def new_document(lead, by_id):
    """The newly published document a lead is built on, for the email's first line."""
    for fact in lead.get('established', []):
        doc = by_id.get(fact.get('source'))
        if doc is not None and doc.published:
            return {'title': doc.title, 'url': doc.url, 'published': doc.published, 'approximate': doc.published_approximate}
    return {}


def to_issue_records(result, docs, geography):
    """Map approved engine leads to the existing Lead/Evidence records and email layout."""
    by_id = {d.id: d for d in docs}
    evidence, leads, seen = [], [], {}

    def evidence_id(source, quote):
        key = (source, _norm(quote))
        if key not in seen:
            doc = by_id[source]
            seen[key] = f'E{len(seen) + 1}'
            evidence.append(Evidence(seen[key], doc.url, doc.title, quote, doc.retrieved_at or 'unknown', doc.sha256, 'text', doc.format or 'text', True))
        return seen[key]

    for n, lead in enumerate(result.approved, 1):
        ids = [evidence_id(e['source'], e['quote']) for e in lead['_evidence']]
        first = lead['established'][0]
        essential = [r for r in lead['records'] if r.get('essential')]
        request = next((r for r in essential if r['route'] == 'records_request'), None)
        public = next((r for r in essential if r['route'] == 'public_download'), None)
        tier = lead.get('_tier', 'ready')
        records = [{'description': r['description'], 'could_reveal': r.get('could_reveal', ''), 'route': r['route'],
                    'location': r.get('location', ''), 'location_basis': r.get('location_basis', ''),
                    'linked_from': r.get('linked_from', ''), 'link_digest': r.get('link_digest', ''),
                    'how': r.get('how', ''), 'essential': r['essential'],
                    'evidence_id': evidence_id(r.get('route_source', r['source']), r.get('route_quote', r['quote']))
                    if r.get('essential') and r.get('location_basis') else ''}
                   for r in lead['records'] if r['route'] != 'unknown' or r.get('essential')]
        submission = request['location'] if request and re.match(r'https?://', request.get('location', '')) else ''
        if submission and submission not in {e.url for e in evidence}:
            submission = ''
        leads.append(Lead(
            id=f'L{n}', provisional_headline=lead['headline'].strip(),
            story_potential=lead.get('hypothesis', '').strip() or 'Unproven; see records below.',
            factual_hook=hook_text(lead['established']),
            evidence_ids=list(dict.fromkeys(ids)), source_url=by_id[first['source']].url,
            likely_records=[r['description'] for r in essential],
            acquisition_route='records_request' if request else 'public',
            acquisition_details=(request or public or {}).get('how', '').strip() or 'See records below.',
            newsworthiness=lead.get('newsworthiness', 5),
            obtainability=6 if request else 8,
            next_action=(public or request or {}).get('how', '').strip() or 'Obtain the records below.',
            reviewer_verdict='approved', jurisdiction=geography,
            records_request_text=(lead.get('request_text') or '').strip() if request else '',
            records_custodian=(request or {}).get('custodian', '').strip(),
            records_submission_url=submission,
            caveat=' '.join(q['text'].strip() for q in lead.get('qualifications', [])[:2]),
            records_plan={'engine': ENGINE_ID, 'records': records, 'new_document': new_document(lead, by_id),
                          'tier': tier, 'route_notes': lead.get('_route_notes', []),
                          'already_answered': lead.get('already_answered', '').strip()}))
    return leads, evidence


def validate_engine_artifacts(issue, sources):
    """Delivery-time check of saved engine artifacts: every evidence excerpt must be the
    literal text of a saved source with the same URL and content hash."""
    by_key = {}
    for raw in sources:
        doc = SourceDoc(**raw) if isinstance(raw, dict) else raw
        by_key[(doc.url, doc.sha256)] = doc
    for ev in issue.evidence:
        doc = by_key.get((ev.url, ev.content_hash))
        if doc is None:
            raise ValueError('Evidence differs from saved source text')
        if _norm(ev.excerpt) not in _norm(doc.text):
            raise ValueError('Evidence excerpt is not literal source text')


def validate_engine_lead(lead, evidence, allowed_acronyms=DEFAULT_ALLOWED_ACRONYMS):
    """Re-check an engine lead at render time against its own evidence records."""
    by_id = {e.id: e for e in evidence if e.id in lead.evidence_ids}
    shown = presentation_problems(lead.provisional_headline, allowed_acronyms)
    shown += headline_grounding(lead.provisional_headline, [e.excerpt for e in by_id.values()] + [e.title for e in by_id.values()],
                                lead.jurisdiction)
    if shown:
        raise ValueError('Engine lead headline: ' + '; '.join(shown))
    records = lead.records_plan.get('records')
    if not isinstance(records, list) or not any(r.get('essential') for r in records):
        raise ValueError('Engine lead requires essential records')
    verify_route = lead.records_plan.get('tier') == 'verify_route'
    if verify_route and not lead.records_plan.get('route_notes'):
        raise ValueError('Route-unverified lead must say what to confirm')
    for record in records:
        if not record.get('essential'):
            continue
        ev = by_id.get(record.get('evidence_id'))
        if verify_route and ev is None:
            continue  # shown as "to confirm"; any route that was checked is still re-checked below
        if record.get('route') not in ('public_download', 'records_request') or ev is None:
            raise ValueError('Engine lead essential record lacks a supported route')
        basis = record.get('location_basis')
        doc = SourceDoc('x', ev.url, ev.title, ev.excerpt)
        if basis == 'links':
            # Checked against the cited page's link list during analysis; the digest binds
            # that result to this location, page and page text.
            # The location may be a name plus an attested URL; render links only bare URLs.
            if (record.get('linked_from') != ev.url
                    or record.get('link_digest') != link_digest(record['location'], ev.url, ev.content_hash)):
                raise ValueError('Engine lead linked access does not match the page it was checked against')
        elif basis not in ('quote', 'source_url') or location_basis(record.get('location', ''), ev.excerpt, doc) != basis:
            raise ValueError('Engine lead access location is not in its evidence')
    if lead.acquisition_route == 'records_request':
        problems = request_problems(lead.records_request_text.strip())
        if problems or len(lead.records_request_text.strip()) < 100:
            raise ValueError('Engine lead request language: ' + '; '.join(problems or ['missing']))


# ---------------------------------------------------------------- discovery and preview

def fetcher(config, provider):
    """fetch(url) -> receipt dict, through the guarded public retriever and, when the
    newsroom enables it, the Firecrawl fallback (many federal sites block plain clients)."""
    from .firecrawl import FirecrawlRetriever, check_environment
    from .retrieval import PublicRetriever
    from .watch import retriever_fetch
    retriever = getattr(provider, 'retriever', None) or PublicRetriever(provider.budget)
    key = check_environment(config)
    if key:
        retriever = FirecrawlRetriever(retriever, config.retrieval, key, getattr(provider, 'firecrawl_transport', None))
    return retriever_fetch(retriever)


def discover(provider, fetch, *, brief, geography, playbook=None, preferred=(), searches=4, max_sources=8):
    """Search mode: one native-search call proposes URLs; the guarded retriever fetches and
    reads them. Search results are leads to documents, never evidence. Order: newsroom
    sources, search results, then playbook starting points."""
    from .retrieval import document_identity
    context = {'brief': brief, 'geography': geography, 'preferred_sources': list(preferred), 'playbook': playbook}
    prompt = 'UNTRUSTED NEWSROOM DATA JSON:\n' + json.dumps(context, ensure_ascii=False)
    before = asdict(provider.budget.snapshot())
    _, urls, _ = provider._request(prompt, _prompt('evidence-discovery'), 'research_requests', searches, searches)
    after = asdict(provider.budget.snapshot())
    priority = [u for u in getattr(provider, 'native_source_priority', []) if u in urls]
    found = list(dict.fromkeys(priority + sorted(urls)))
    docs, not_read, seen = [], [], set()
    for url in dict.fromkeys([*preferred, *found, *playbook_urls(playbook)]):
        if len(docs) >= max_sources:
            break
        identity = document_identity(url)
        if identity in seen:
            continue
        seen.add(identity)
        receipt = fetch(url)
        if receipt and receipt.get('sections'):
            docs.append(source_from_receipt(receipt, f's{len(docs)}'))
        else:
            not_read.append({'url': url, 'reason': (receipt or {}).get('coverage', 'unreadable')})
    meta = {'stage': 'discovery', 'model': provider.model, 'usage': {k: after[k] - before[k] for k in after},
            'search_urls': found, 'read': [d.url for d in docs], 'not_read': not_read}
    return docs, meta


def _watch_run(config, provider, playbook, allowed, brief, sink, local_date, record_seen, seen_override=None):
    """Watch mode: newly published documents from the watch list, each analysed with the
    newsroom's standing request pages, leads required to rest on the new document."""
    from datetime import date as _date
    from .watch import collect_new, load_seen, save_seen
    today = local_date or _date.today()
    fetch = fetcher(config, provider)
    watch_urls = list(dict.fromkeys(config.hunt.watch + list((playbook or {}).get('watch', []))))
    request_urls = list(dict.fromkeys(config.hunt.request_pages + list((playbook or {}).get('request_pages', []))))
    if not watch_urls:
        # Otherwise every run would quietly report "no new documents".
        raise ValueError('Watch mode needs pages to watch: set [hunt] watch or a playbook with a watch list')
    # Request pages are fetched first so they get the fallback allowance before the
    # documents do: a follow-on request is useless without its route.
    fetched_requests = [r for r in (fetch(u) for u in request_urls) if r and r.get('sections')]
    request_docs = [source_from_receipt(r, f'r{i}') for i, r in enumerate(fetched_requests)]
    # Scheduled runs pass the newsroom's committed state; local previews use seen_path.
    seen = dict(seen_override) if seen_override is not None else load_seen(config.hunt.seen_path)
    keywords = list(dict.fromkeys(config.hunt.keywords + list((playbook or {}).get('watch_keywords', []))))
    report = collect_new(fetch, watch_urls, today=today, window_days=config.hunt.window_days, seen=seen,
                         max_new=config.hunt.max_new_documents, keywords=keywords)
    for doc in request_docs:
        # Request pages go first and are capped, so a long new document cannot crowd out
        # the routes its follow-on requests need; the new document gets the rest, with
        # its beginning and end kept if it must be cut.
        if len(doc.text) > REQUEST_PAGE_CHARS:
            doc.text, doc.note = doc.text[:REQUEST_PAGE_CHARS], 'request page trimmed to its opening text'
    approved, held, calls, partial, reasons, analysed = [], [], [], [], [], []
    from .budget import BudgetExceeded
    from .providers.base import ProviderError
    for doc in report.new:
        def doc_sink(stage, text, meta, doc_id=doc.id):
            sink(f'{doc_id}-{stage}', text, meta)
        try:
            result = analyse(provider, brief=brief, geography=config.editorial.geography, docs=request_docs + [doc],
                             playbook=playbook, allowed_acronyms=allowed, max_source_chars=config.hunt.max_source_chars,
                             sink=doc_sink, fresh_ids={doc.id}, today=today.isoformat(),
                             reviewer=getattr(provider, 'reviewer', None))
        except EngineError as exc:
            held.append({'headline': doc.title, 'status': 'run_error', 'problems': [f'malformed model output: {exc}']})
            analysed.append(doc)  # analysed (and paid for); do not repeat it
            continue
        except (BudgetExceeded, ProviderError) as exc:
            if not analysed:
                raise  # nothing analysed: a run failure, never a "no leads" email
            # Keep what earlier documents produced; stop spending. Unanalysed documents
            # stay new for the next run.
            held.append({'headline': doc.title, 'status': 'run_error', 'problems': [f'{type(exc).__name__}: {exc}']})
            break
        analysed.append(doc)
        approved += result.approved; held += result.held; calls += result.calls; partial += result.partial_sources
        if result.no_lead_reason:
            reasons.append(f'{doc.title}: {result.no_lead_reason}')
    if record_seen:
        # Only documents actually analysed; ones skipped by a mid-run failure stay new.
        seen.update({u: today.isoformat() for d in analysed for u in (d.url, d.listed_url) if u})
        save_seen(config.hunt.seen_path, seen, today)
    notes = [f'{len(report.new)} new document(s) in the last {config.hunt.window_days} days on the watch list']
    notes += [f"skipped {s['url']}: {s['reason']}" for s in report.skipped[:20]]
    approved.sort(key=lambda a: a.get('_tier') != 'ready')  # ready leads first across documents, before any cut
    reason = '; '.join(reasons) or ('' if report.new else f'No new documents in the last {config.hunt.window_days} days')
    analysed_urls = list(dict.fromkeys(u for d in analysed for u in (d.url, d.listed_url) if u))
    return report.new + request_docs, EngineResult(approved, held, reason, partial, calls, analysed_urls), notes


def hunt_preview(config, provider, output_dir, *, docs=None, local_date=None, record_seen=False, seen=None, raw_dir=None):
    """Run the evidence engine for a newsroom and write preview files. No delivery.

    Raw model outputs are written as each call returns, and the engine result is written
    before rendering, so a later validation failure never loses paid output."""
    from datetime import date as _date
    from .models import Issue
    from .render import render_issue
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = Path(raw_dir) if raw_dir else out
    raw.mkdir(parents=True, exist_ok=True)

    def sink(stage, text, meta):
        (raw / f'stage-{stage}.json').write_text(json.dumps({'meta': meta, 'output_text': text}, ensure_ascii=False, indent=1) + '\n',
                                                 encoding='utf-8')

    playbook = load_playbook(config.hunt.playbook) if config.hunt.playbook else None
    allowed = (*DEFAULT_ALLOWED_ACRONYMS, *config.hunt.allowed_acronyms)
    calls = []
    brief = config.editorial.coverage_brief + (f'\nExclude: {config.editorial.exclusions}' if config.editorial.exclusions else '')
    if config.hunt.mode == 'watch' and docs is None:
        docs, result, watch_notes = _watch_run(config, provider, playbook, allowed, brief, sink, local_date, record_seen, seen)
        calls.extend(result.calls)
        result.calls = []
    else:
        watch_notes = []
        if docs is None:
            docs, meta = discover(provider, fetcher(config, provider), brief=config.editorial.coverage_brief,
                                  geography=config.editorial.geography,
                                  playbook=playbook, preferred=[s.url for s in config.sources])
            calls.append(meta)
        result = analyse(provider, brief=brief, geography=config.editorial.geography, docs=docs, playbook=playbook,
                         allowed_acronyms=allowed, max_source_chars=config.hunt.max_source_chars, sink=sink,
                         reviewer=getattr(provider, 'reviewer', None))
    result.approved = result.approved[:config.editorial.max_final_leads]
    leads, evidence = to_issue_records(result, docs, config.editorial.geography)
    # The render-time re-check must agree with the gate. If it does not, hold the lead
    # with the reason rather than crash or deliver it.
    kept = []
    for record, lead in zip(result.approved, leads):
        try:
            validate_engine_lead(lead, evidence, allowed)
            kept.append(lead)
        except ValueError as exc:
            result.held.append({'headline': lead.provisional_headline, 'status': 'render_rejected', 'problems': [str(exc)]})
    leads = kept
    # The exact source texts the model saw, so any run can be re-checked offline later.
    (out / 'analysed-documents.json').write_text(json.dumps(result.analysed) + '\n', encoding='utf-8')
    (out / 'sources.json').write_text(json.dumps([asdict(d) for d in docs], ensure_ascii=False) + '\n', encoding='utf-8')
    (out / 'engine-result.json').write_text(json.dumps({
        'engine': ENGINE_ID, 'approved': [{k: v for k, v in a.items() if k != '_evidence'} for a in result.approved],
        'emailed': [l.provisional_headline for l in leads],
        'held': result.held, 'no_lead_reason': result.no_lead_reason, 'partial_sources': result.partial_sources,
        'calls': calls + result.calls, 'watch_notes': watch_notes,
        'sources': [{'id': d.id, 'url': d.url, 'chars': len(d.text), 'note': d.note, 'published': d.published} for d in docs],
    }, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    notes = watch_notes + [f"{h['status']}: {h['headline'] or 'untitled'}" for h in result.held] or ['Completed evidence-engine run']
    if not leads:
        notes.append(result.no_lead_reason or 'No qualifying document leads')
    # Same key format as the scheduler ('id:YYYY-MM-DD') so delivery accepts it.
    issue = Issue(f'{config.identity.id}:{(local_date or _date.today()).isoformat()}', local_date or _date.today(),
                  evidence, leads, notes, leads[0].provisional_headline if leads else 'No qualifying document leads',
                  leads[0].id if leads else None)
    rendered = render_issue(issue, config)
    (out / 'preview.html').write_text(rendered.html, encoding='utf-8')
    (out / 'preview.txt').write_text(rendered.text, encoding='utf-8')
    return issue, result
