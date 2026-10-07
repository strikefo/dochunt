"""Find new documents on a newsroom's watch list.

Each run reads the configured watch pages through the guarded public retriever and keeps
documents published within the freshness window that have not been seen before. Feeds
(RSS/Atom) give exact item dates. On HTML listing pages, a bounded number of candidate
document links on the same site are fetched, and each document's own labelled date is
read. A document without a determinable date inside the window is not "new".

Nothing here calls a model. Dates are evidenced by a literal snippet from the feed item or
document text.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .engine import SourceDoc, _norm, source_from_receipt

MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
          'october', 'november', 'december')
_MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
# "Posted" is not trusted: old pages are often re-posted. It falls to the unlabelled rules.
_LABEL = r'(?:Published|Issued(?: Date)?|Date Issued|Released|Release Date|For release|Report Date)'
_DOCUMENT_PATH = re.compile(r'\.(?:pdf|htm|html)$|/(?:reports?|audits?|publications?|news|releases?|documents?|products?)/', re.I)


@dataclass
class Dated:
    value: date
    evidence: str
    approximate: bool = False


def _parse_day(text):
    text = _norm(text)
    m = re.match(rf'({_MONTH})\s+(\d{{1,2}}),?\s+(\d{{4}})', text, re.I)
    if m:
        return date(int(m.group(3)), MONTHS.index(m.group(1).casefold()) + 1, int(m.group(2)))
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', text)
    if m:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    return None


def feed_item_date(raw):
    """Date of an RSS (RFC 822) or Atom (ISO 8601) item."""
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date()
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except ValueError:
        return _parse_day(raw)


def document_date(text):
    """A document's publication date from its own opening text, conservatively:

    1. a labelled full date ("Issued Date October 02, 2026", "DATE ISSUED: SEPTEMBER 30,
       2025", "Published: April 9, 2024", "For release ... January 22, 2026"), ignoring
       labels qualified as revised, updated, effective, review or last;
    2. a labelled month and year ("Issued March 2021"), as the first of that month;
    3. otherwise the EARLIEST unlabelled full date in the opening text that is not a
       deadline, schedule or update banner ("by", "until", "scheduled", "reviewed"...),
       and only if the opening text mentions no earlier year (report numbers and fiscal
       years included); otherwise the document is treated as undated;
    4. otherwise the earliest month and year under the same conditions, first of month.
    Earliest-wins and first-of-month can make a new document look older (it is then
    skipped); they never make an old document look new."""
    head = _norm(text)[:3000]
    day = rf'({_MONTH}\s+\d{{1,2}},?\s+\d{{4}}|\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}})'
    month_year = rf'({_MONTH})\s+(\d{{4}})\b'

    def qualified(start):
        return re.search(r'(?:revis\w*|updat\w*|effective|review\w*|last|amend\w*|modif\w*)\W{0,3}$', head[max(0, start - 20):start], re.I)

    def deadline(start):
        return re.search(r'(?:\bby|\bbefore|\buntil|\bdue|no later than|deadline|updated|revised|reviewed|modified|accessed|'
                         r'printed|changed|effective|expires?|scheduled|begins?|starts?|will|planned|hearing|meeting|posted)'
                         r'\W{0,3}(?:\w+\W+){0,3}$',
                         head[max(0, start - 30):start], re.I)

    def evidence(m):
        return head[max(0, m.start() - 40):m.end() + 20].strip()

    # Only a release time and weekday may sit between a label and its date ("For release
    # 10:00 a.m. (ET) Thursday, January 22, 2026"); nothing else, so another banner's
    # date ("Report Date: see cover. Page reviewed October 3, 2026") is never taken.
    filler = (r'(?:\d{1,2}:\d{2}\s*[ap]\.?m\.?\s*(?:\(\w+\)\s*)?)?'
              r'(?:(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,?\s*)?')
    for m in re.finditer(rf'\b{_LABEL}\s*:?\s*{filler}{day}', head, re.I):
        if qualified(m.start()):
            continue
        value = _parse_day(m.group(1).title() if m.group(1)[0].isalpha() else m.group(1))
        if value:
            return Dated(value, evidence(m))
    for m in re.finditer(rf'\b{_LABEL}\s*:?\s*{month_year}', head, re.I):
        if qualified(m.start()):
            continue
        month = MONTHS.index(m.group(1).casefold()) + 1
        return Dated(date(int(m.group(2)), month, 1), evidence(m), approximate=True)
    opening = head[:1500]

    def older_year_mentioned(value):
        # An unlabelled date is trusted only if the opening text mentions no earlier
        # year at all, report numbers ("DODIG-2021-055") and fiscal years included.
        return any(int(y) < value.year for y in re.findall(r'(?<!\d)((?:19|20)\d{2})(?!\d)', opening))

    dates = []
    for m in re.finditer(rf'\b({_MONTH}\s+\d{{1,2}},?\s+\d{{4}})', opening):
        value = _parse_day(m.group(1))
        if value and not deadline(m.start()):
            dates.append((value, m))
    if dates:
        value, m = min(dates, key=lambda pair: pair[0])
        return None if older_year_mentioned(value) else Dated(value, evidence(m))
    months = [(date(int(m.group(2)), MONTHS.index(m.group(1).casefold()) + 1, 1), m)
              for m in re.finditer(rf'\b{month_year}', head[:1500]) if not deadline(m.start())]
    if months:
        value, m = min(months, key=lambda pair: pair[0])
        return None if older_year_mentioned(value) else Dated(value, evidence(m), approximate=True)
    return None


def within(value, today, window_days):
    return today - timedelta(days=window_days) <= value <= today


@dataclass
class WatchReport:
    new: list = field(default_factory=list)          # SourceDoc with .published set
    skipped: list = field(default_factory=list)      # {'url', 'reason'}


def load_seen(path):
    try:
        data = json.loads(Path(path).read_text())
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def save_seen(path, seen, today, keep_days=180):
    """Remember documents already reported; forget entries older than keep_days."""
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    kept = {k: v for k, v in seen.items() if v >= cutoff}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(kept, indent=1, sort_keys=True) + '\n')


def _doc(receipt, doc_id, published):
    doc = source_from_receipt(receipt, doc_id)
    doc.published = published.value.isoformat()
    doc.published_evidence = published.evidence
    doc.published_approximate = published.approximate
    return doc


def _mentions(title, keywords):
    """Whole-word, case-insensitive match of any beat keyword in a feed item title."""
    return any(re.search(r'\b' + re.escape(k) + r'\b', title or '', re.I) for k in keywords)


def _why(receipt):
    return f" ({receipt['coverage']})" if receipt and receipt.get('coverage') else ''


def collect_new(fetch, watch_urls, *, today, window_days=14, seen=None, max_candidates_per_page=8, max_new=5, keywords=()):
    """fetch(url) -> receipt dict (or None). Returns new documents, newest first."""
    seen = dict(seen or {})
    report = WatchReport()
    found = []
    for page_url in watch_urls:
        page = fetch(page_url)
        if not page or not page.get('sections'):
            report.skipped.append({'url': page_url, 'reason': 'watch page unreadable' + _why(page)})
            continue
        if page.get('format') == 'feed':
            for section in page['sections']:
                published_raw, _, rest = section['text'].partition(' | ')
                title, _, link = rest.rpartition(' | ')
                if not link or link in seen:
                    continue
                if keywords and not _mentions(title, keywords):
                    report.skipped.append({'url': link, 'reason': 'off-topic by title'})
                    continue
                when = feed_item_date(published_raw)
                if when is None or not within(when, today, window_days):
                    report.skipped.append({'url': link, 'reason': 'outside window' if when else 'undated feed item'})
                    continue
                found.append((link, Dated(when, f'feed item dated {published_raw}')))
        else:
            host = urlsplit(page_url).hostname
            candidates = [l for l in dict.fromkeys(page.get('links', []))
                          if urlsplit(l).hostname == host and l != page_url and l not in seen and _DOCUMENT_PATH.search(urlsplit(l).path)]
            for link in candidates[:max_candidates_per_page]:
                found.append((link, None))
    results = []
    for link, dated in found:
        if len(results) >= max_new:
            break
        receipt = fetch(link)
        if not receipt or not receipt.get('sections'):
            report.skipped.append({'url': link, 'reason': 'document unreadable' + _why(receipt)})
            continue
        text = ' '.join(s.get('text', '') for s in receipt['sections'])
        own = document_date(text)
        # A feed or listing date never overrides an older date in the document itself:
        # a re-posted or re-listed old report stays old.
        if dated is None or (own is not None and own.value < dated.value):
            dated = own
        if dated is None:
            report.skipped.append({'url': link, 'reason': 'no publication date found'})
            continue
        if not within(dated.value, today, window_days):
            report.skipped.append({'url': link, 'reason': f'published {dated.value.isoformat()}, outside window'})
            continue
        doc = _doc({**receipt, 'url': receipt.get('url') or link}, f'n{len(results) + 1}', dated)
        doc.listed_url = link  # seen-state records both the listed and the final URL
        results.append(doc)
    results.sort(key=lambda d: d.published, reverse=True)
    report.new = results
    return report


def retriever_fetch(retriever):
    """Adapt the guarded PublicRetriever to collect_new's fetch(url) -> receipt."""
    from .retrieval import RetrievalBlocked

    from .budget import BudgetExceeded

    def fetch(url):
        try:
            # Watch runs use the whole fallback allowance; there is no later
            # verification pass in this mode that needs a reserve.
            result = retriever.fetch(url, independent=True) if hasattr(retriever, 'attempts') else retriever.fetch(url)
        except (RetrievalBlocked, BudgetExceeded) as exc:
            if retriever.budget.blocked:
                raise  # paid fallback usage is uncertain: stop the run, never a quiet skip
            return {'url': url, 'sections': [], 'coverage': f'{type(exc).__name__}: {exc}'}
        if result.content:
            return asdict(result)
        return {'url': url, 'sections': [], 'coverage': result.coverage}  # keep the reason (e.g. HTTP 403)
    return fetch
