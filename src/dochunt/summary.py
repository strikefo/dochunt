"""A readable preview on the Actions run page, so nobody has to download a zip to review it.

Everything here is written as escaped HTML. The email text comes from the research agent,
which read untrusted web pages, so none of it may be read as Markdown or markup. Each part is
its own Markdown HTML block (separated by blank lines): a <pre> block then runs to </pre>, which
escaped text can never contain, so blank lines inside the email can't end it early.
"""
from html import escape
import json
import math
from pathlib import Path

# GitHub caps a step summary at 1 MiB. Escaping turns one character into at most six UTF-8 bytes
# (' becomes &#x27;), so 100,000 characters stay under 600 KB. A real issue is a few kilobytes.
MAX_TEXT = 100_000
MAX_SUBJECT = 300


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _listed(value):
    return value if isinstance(value, list) else []


def _number(value):
    """A finite, non-negative number, or 0 for anything else (bools, NaN, infinity, text)."""
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    return value if ok else 0


def _facts(issue, report):
    """One line of run details. Every field is optional; a wrong type counts as missing."""
    facts = []
    if isinstance(issue.get('leads'), list):
        facts.append(f'{len(issue["leads"])} lead(s) in the email')
    if isinstance(report.get('dropped'), list):  # 0 too: a clean report differs from a missing one
        facts.append(f'{len(report["dropped"])} dropped by the checks')
    if _listed(report.get('warnings')):
        facts.append(f'{len(report["warnings"])} warning(s)')
    attempts = [a for a in _listed(report.get('attempts')) if isinstance(a, dict)]
    seconds = sum(_number(a.get('seconds')) for a in attempts)
    if seconds:
        facts.append(f'{round(seconds / 60, 1)} min of agent time')
    cost = sum(_number(a['usage'].get('cost_usd')) for a in attempts if isinstance(a.get('usage'), dict))
    if cost:
        facts.append(f'about ${cost:.2f} reported by Claude Code')
    if any('error' in a and not a.get('skipped') for a in attempts) and (seconds or cost):
        # A repair that started and failed records no usage, so these figures may not be the whole run.
        # A skipped repair never called the provider, so it leaves the totals complete.
        facts.append('time and cost cover only attempts that finished; check your provider\'s usage page')
    return facts


def preview_summary(output_dir, run_id=''):
    output_dir = Path(output_dir)
    text = (output_dir / 'preview.txt').read_text(encoding='utf-8')
    manifest = _read_json(output_dir / 'preview-manifest.json')
    issue = _read_json(output_dir / 'issue.json').get('issue')
    issue = issue if isinstance(issue, dict) else {}
    report = _read_json(output_dir / 'check-report.json')
    mode = manifest.get('mode') if isinstance(manifest.get('mode'), str) else ''
    paid = mode == 'paid-ai-preview'
    title = {'paid-ai-preview': 'Paid AI preview',
             'synthetic-preview': 'Fictional preview (not real research)'}.get(mode, 'Preview (type unknown)')

    try:
        facts = _facts(issue, report)
    except Exception:  # the details are a convenience; the email itself must always show
        facts = ['run details unavailable; see check-report.json']

    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + '\n[cut short here; the full text is in preview.txt]'
    parts = [f'<h2>{escape(title)}</h2>',
             f'<p><strong>Subject:</strong> {escape(str(issue.get("subject", ""))[:MAX_SUBJECT])}<br>{escape("; ".join(facts))}</p>']
    if paid and run_id:
        parts.append(f'<p>To send this exact preview to one configured recipient as a test, run the workflow again '
                     f'within 7 days (while this run\'s artifacts are kept) with mode <code>send-preview-test</code> '
                     f'and preview run ID <code>{escape(str(run_id))}</code>. Enter your own address from the '
                     'recipients list; a blank recipient means the first address on that list, whoever it belongs to.</p>')
    extra = ', <code>check-report.json</code> (dropped leads, warnings, token usage) and the agent\'s raw output' if paid else ''
    parts.append(f'<p>The email as plain text. The formatted version{extra} {"are" if paid else "is"} in this run\'s artifacts.</p>')
    parts.append(f'<pre>{escape(text)}</pre>')
    return '\n\n'.join(parts) + '\n'
