"""Deterministic checks between the agent and the inbox.

Deliberately small. A lead is dropped only for problems a reader would trip
over: a broken or missing link, no usable first action, an unverified route, or
a repeat of an earlier issue. Everything else (unreachable links, which many
government sites cause by blocking bots; headline numbers) is a warning that is
recorded in the run's check report, never a reason to drop a lead.
"""
from dataclasses import dataclass, field
import re
from .models import (NO_LEAD_SUBJECT, public_url, Issue, IssueFormatError, issue_from_parts, lead_problems, parse_lead,
                     source_key, subject_problems)

MIN_SOURCES_FOR_NO_LEADS = 3
NO_LEAD_FALLBACK = 'Candidate leads were found but did not pass DocHunt\'s link and route checks.'


@dataclass
class Assessment:
    issue_problems: list[str] = field(default_factory=list)
    leads: list = field(default_factory=list)          # [(Lead|None, [problems])], in agent order
    subject: str = ''
    intro: str = ''
    no_lead_reason: str = ''
    checked_sources: list = field(default_factory=list)

    @property
    def problems(self):
        return self.issue_problems + [p for _, found in self.leads for p in found]


def assess(data, *, max_leads, sent):
    """Check agent JSON against the schema and reader-facing rules. Never raises."""
    a = Assessment()
    if not isinstance(data, dict):
        a.issue_problems.append('the output must be a JSON object')
        return a
    extra = set(data) - {'subject', 'intro', 'no_lead_reason', 'checked_sources', 'leads'}
    if extra:
        a.issue_problems.append(f'issue has unexpected fields: {", ".join(sorted(extra))}')
    for name in ('subject', 'intro', 'no_lead_reason'):
        value = data.get(name, '')
        if not isinstance(value, str):
            a.issue_problems.append(f'{name} must be a string')
            value = ''
        if name != 'subject':
            # Same rule the saved-artifact parser applies: line breaks become \n, other controls are removed.
            value = value.replace('\r\n', '\n').replace('\r', '\n')
            value = ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)
        setattr(a, name, value.strip())
    a.issue_problems += subject_problems(a.subject)
    checked = data.get('checked_sources')
    if not isinstance(checked, list) or any(not isinstance(u, str) for u in checked):
        a.issue_problems.append('checked_sources must be a list of the URLs you opened during this run')
        checked = []
    keys = set()
    for url in checked:
        try:
            url = public_url(url.strip())
        except ValueError:
            continue
        if source_key(url) not in keys:  # #page=2 or ?utm_ variants are the same source
            keys.add(source_key(url))
            a.checked_sources.append(url)
    if not a.checked_sources:
        a.issue_problems.append('checked_sources is empty: list the official pages and documents you opened during this run')
    leads = data.get('leads')
    if not isinstance(leads, list):
        a.issue_problems.append('leads must be a list')
        leads = []
    if len(leads) > max_leads:
        a.issue_problems.append(f'there are {len(leads)} leads; keep only the strongest {max_leads}')
    seen = set()
    for i, item in enumerate(leads, 1):
        where = f'lead {i}'
        lead, found = parse_lead(item, where)
        if lead is not None:
            found += lead_problems(lead, where)
            key = source_key(lead.source_url)
            if key in seen:
                found.append(f'{where}: repeats the source document of an earlier lead in this issue')
            elif key in sent and not lead.is_update:
                found.append(f'{where}: this source was already sent in an earlier issue; drop it or mark it as an update with new information')
            if not found:
                seen.add(key)  # Only a lead that passes reserves its document; a rejected one can't block a valid one.
        a.leads.append((lead, found))
    if not leads and not a.no_lead_reason:
        a.issue_problems.append('an issue without leads needs a short no_lead_reason')
    if a.checked_sources and len(a.checked_sources) < MIN_SOURCES_FOR_NO_LEADS and not any(l and not f for l, f in a.leads):
        a.issue_problems.append(f'with no qualifying leads, checked_sources must list at least {MIN_SOURCES_FOR_NO_LEADS} '
                                'distinct pages or documents you opened during this run')
    return a


def _trim_subject(text):
    text = ' '.join(text.split())
    if len(text) <= 100:
        return text
    cut = text[:99].rsplit(' ', 1)[0].rstrip(',;:-')
    return cut + '…'


def finalize(a, *, issue_key, local_date, max_leads):
    """Build the sendable Issue from an assessment, dropping failed leads.

    Returns (issue, dropped) where dropped lists {lead, problems}. Raises
    IssueFormatError if no valid issue can be built (the run then sends nothing).
    """
    if any(p.startswith('the output must') or p == 'leads must be a list' for p in a.issue_problems):
        raise IssueFormatError(a.issue_problems)
    research_shown = len(a.checked_sources) >= MIN_SOURCES_FOR_NO_LEADS
    kept, dropped = [], []
    for lead, found in a.leads:
        if lead is not None and not found and len(kept) < max_leads:
            kept.append(lead)
        else:
            dropped.append({'headline': lead.headline if lead else '', 'problems': found or ['beyond the lead limit']})
    # The subject summarizes the top lead; if that lead was dropped, the subject is about the wrong story.
    top_dropped = bool(a.leads) and not (kept and a.leads[0][0] is kept[0])
    subject = a.subject
    if not kept:
        subject = NO_LEAD_SUBJECT
    elif top_dropped or subject_problems(subject) or subject == NO_LEAD_SUBJECT:
        subject = _trim_subject(kept[0].headline)
    elif dropped and ';' in subject:
        # The prompt allows a second lead's highlight after a semicolon; that lead may be gone.
        subject = subject.split(';', 1)[0].strip()
    if not kept and not research_shown:
        # A "nothing today" email is only honest if research actually happened. Without it the
        # likely cause is broken web access, so fail the run visibly instead of sending.
        raise IssueFormatError([f'The agent returned no leads but listed {len(a.checked_sources)} checked source(s); '
                                'nothing was sent. Check that the agent has web access, then rerun.'
                                + (f' Agent said: {a.no_lead_reason}' if a.no_lead_reason else '')])
    reason = '' if kept else (a.no_lead_reason or NO_LEAD_FALLBACK)
    # The agent's intro describes the leads it chose; once any are dropped it may be wrong.
    intro = a.intro if not dropped else ''
    return issue_from_parts(issue_key, local_date, subject, intro, reason, kept), dropped


def _numbers(text):
    return {n.replace(',', '') for n in re.findall(r'\d[\d,]*(?:\.\d+)?', text)}


def warnings_for(issue, fetch=None, *, time_left=None):
    """Advisory findings for the operator. Never changes the issue.

    ``time_left`` returns the seconds still available; link probes stop when it runs
    low, so advisory checks can never cost the run its delivery.
    """
    found = []
    for i, lead in enumerate(issue.leads, 1):
        missing = _numbers(lead.headline) - _numbers(lead.reporting_opportunity + ' ' + lead.source_title)
        if missing:
            found.append(f'lead {i}: headline number(s) {", ".join(sorted(missing))} do not appear in its reporting opportunity')
    if not fetch:
        return found
    for i, lead in enumerate(issue.leads, 1):
        r = lead.get_records
        for label, url in (('source', lead.source_url), ('contact', r.contact.url), ('verification page', r.verified_at_url)):
            if not url:
                continue
            if time_left is not None and time_left() < LINK_CHECK_RESERVE:
                found.append('Remaining link checks skipped: not enough time before the run deadline')
                return found
            status = _bounded(fetch, url, min(LINK_PROBE_LIMIT, time_left() - LINK_CHECK_RESERVE) if time_left else LINK_PROBE_LIMIT)
            if status is not None and status >= 400 and status not in (401, 403, 405, 429):
                found.append(f'lead {i}: {label} link returned HTTP {status}: {url}')
    return found


LINK_CHECK_RESERVE = 90
LINK_TIMEOUT = 10
LINK_PROBE_LIMIT = 20


def _bounded(fn, url, limit):
    """Call fn(url) with a hard wall-clock limit (DNS included); None if it doesn't finish."""
    import threading
    result = [None]

    def probe():
        try:
            result[0] = fn(url)
        except Exception:
            result[0] = None
    worker = threading.Thread(target=probe, daemon=True)
    worker.start()
    worker.join(max(0.0, limit))
    return None if worker.is_alive() else result[0]


def _public_addresses(host, port):
    """Resolved addresses for host, or [] if any of them is not a public internet address."""
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    addresses = []
    for family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if not ip.is_global or ip.is_multicast:
            return []
        addresses.append((family, sockaddr))
    return addresses


def http_status(url, timeout=LINK_TIMEOUT):
    """HTTP status of an agent-supplied link, or None when it can't be determined safely.

    The link came from the web, so it is treated as hostile: only http(s) to public
    internet addresses, the connection is pinned to the address that was checked (no DNS
    rebinding), and redirects are not followed (a 3xx counts as reachable).
    """
    import http.client
    import socket
    import ssl
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        return None
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        return None
    addresses = _public_addresses(parts.hostname, port)
    if not addresses:
        return None
    family, sockaddr = addresses[0]
    path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
    sock = None
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(sockaddr)
        if parts.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
            conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
        conn.sock = sock
        conn.request('GET', path, headers={'User-Agent': 'Mozilla/5.0 (compatible; DocHunt link check)', 'Accept': '*/*'})
        status = conn.getresponse().status
        conn.close()
        return status
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
