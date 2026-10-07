"""Watch mode: new documents only, follow-on requests built on them.

Feeds and pages are synthetic or saved receipts served by a fake fetcher; no network.
"""
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from dochunt.budget import Budget
from dochunt.config import load_config
from dochunt.engine import check_lead, hunt_preview, source_from_receipt
from dochunt.extraction import extract
from dochunt.models import Limits
from dochunt.providers.base import FixtureTransport
from dochunt.providers.openai import OpenAIProvider
from dochunt.watch import collect_new, document_date, feed_item_date, load_seen, within

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 6)

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Reports</title>
<item><title>New audit</title><link>https://example.gov/reports/new.html</link><pubDate>Fri, 02 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>Old audit</title><link>https://example.gov/reports/old.html</link><pubDate>Mon, 03 Mar 2025 09:00:00 GMT</pubDate></item>
<item><title>Seen audit</title><link>https://example.gov/reports/seen.html</link><pubDate>Sat, 03 Oct 2026 09:00:00 GMT</pubDate></item>
</channel></rss>'''

ATOM = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>News</title>
<entry><title>Release</title><link href="https://example.gov/news/a.html"/><updated>2026-10-01T12:00:00Z</updated></entry></feed>'''


def receipt(url, text, links=(), fmt='html'):
    return {'url': url, 'title': url.rsplit('/', 1)[-1], 'sections': [{'locator': 'text', 'text': text}],
            'links': list(links), 'format': fmt, 'retrieved_at': '2026-10-06T00:00:00Z',
            'content_hash': hashlib.sha256(text.encode()).hexdigest()}


def feed_receipt(url, body):
    parsed = extract(body, 'application/rss+xml', url, 5)
    return {'url': url, **parsed}


def test_rss_and_atom_items_are_extracted_with_dates():
    rss = extract(RSS, 'application/rss+xml', 'https://example.gov/feed', 5)
    assert rss['format'] == 'feed' and len(rss['sections']) == 3 and rss['links'][0].endswith('new.html')
    atom = extract(ATOM, 'application/atom+xml', 'https://example.gov/atom', 5)
    assert atom['links'] == ['https://example.gov/news/a.html']
    assert feed_item_date('Fri, 02 Oct 2026 09:00:00 GMT') == date(2026, 10, 2)
    assert feed_item_date('2026-10-01T12:00:00Z') == date(2026, 10, 1)


def test_feeds_with_entity_declarations_are_refused():
    evil = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel><item><link>&a;</link></item></channel></rss>'
    assert extract(evil, 'application/rss+xml', 'https://example.gov/feed', 5) is None


@pytest.mark.parametrize('source,expected,approximate', [
    ('osc-fonda', date(2026, 10, 2), False),            # "Issued Date October 02, 2026"
    ('dol-oig-child-labor', date(2025, 9, 30), False),  # "DATE ISSUED: SEPTEMBER 30, 2025"
    ('ca-auditor-2023-102-2', date(2024, 4, 9), False),  # "Published: April 9, 2024"
    ('bls-soii-2026', date(2026, 1, 22), False),         # "For release 10:00 a.m. (ET) Thursday, January 22, 2026"
])
def test_document_dates_from_saved_sources(source, expected, approximate):
    dated = document_date(json.loads((ROOT / 'eval/sources' / f'{source}.json').read_text())['text'])
    assert dated.value == expected and dated.approximate is approximate and dated.evidence


def test_feed_keeps_only_unseen_items_inside_the_window():
    pages = {'https://example.gov/feed': feed_receipt('https://example.gov/feed', RSS),
             'https://example.gov/reports/new.html': receipt('https://example.gov/reports/new.html', 'Audit text.'),
             'https://example.gov/reports/old.html': receipt('https://example.gov/reports/old.html', 'Old text.')}
    report = collect_new(pages.get, ['https://example.gov/feed'], today=TODAY,
                         seen={'https://example.gov/reports/seen.html': '2026-10-04'})
    assert [d.url for d in report.new] == ['https://example.gov/reports/new.html']
    assert report.new[0].published == '2026-10-02'
    assert {s['reason'] for s in report.skipped} == {'outside window'}


def test_listing_page_documents_need_their_own_date_inside_the_window():
    listing = receipt('https://example.gov/reports/', 'Reports', links=[
        'https://example.gov/reports/a.pdf', 'https://example.gov/reports/b.pdf',
        'https://example.gov/reports/c.pdf', 'https://other.example/reports/d.pdf'])
    pages = {'https://example.gov/reports/': listing,
             'https://example.gov/reports/a.pdf': receipt('https://example.gov/reports/a.pdf', 'DATE ISSUED: OCTOBER 1, 2026 Findings.'),
             'https://example.gov/reports/b.pdf': receipt('https://example.gov/reports/b.pdf', 'Issued Date June 3, 2024 Findings.'),
             'https://example.gov/reports/c.pdf': receipt('https://example.gov/reports/c.pdf', 'No date anywhere here.')}
    report = collect_new(pages.get, ['https://example.gov/reports/'], today=TODAY)
    assert [d.url for d in report.new] == ['https://example.gov/reports/a.pdf']
    reasons = {s['url'].rsplit('/', 1)[-1]: s['reason'] for s in report.skipped}
    assert reasons['b.pdf'].endswith('outside window') and reasons['c.pdf'] == 'no publication date found'
    assert 'd.pdf' not in reasons  # other sites are never followed


def test_new_document_cap():
    links = [f'https://example.gov/reports/{i}.pdf' for i in range(10)]
    pages = {'https://example.gov/reports/': receipt('https://example.gov/reports/', 'Reports', links=links)}
    pages.update({l: receipt(l, 'Published: October 3, 2026 Text.') for l in links})
    report = collect_new(pages.get, ['https://example.gov/reports/'], today=TODAY, max_new=3)
    assert len(report.new) == 3


def _doc(id_, text, published=''):
    doc = source_from_receipt(receipt(f'https://example.gov/{id_}', text), id_)
    doc.published = published
    return doc


def test_lead_resting_only_on_older_material_is_held_as_stale():
    new = _doc('n1', 'The inspector general found 12 contracts lacked required approvals.', '2026-10-02')
    old = _doc('r0', 'The Records Office of the Example Agency processes all FOIA requests submitted by email to foia@example.gov.')
    fact_old = {'text': 'The agency processes FOIA requests.', 'source': 'r0',
                'quote': 'The Records Office of the Example Agency processes all FOIA requests submitted by email to foia@example.gov.'}
    lead = {'headline': 'Are agency contracts properly approved?', 'established': [fact_old], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [], 'newsworthiness': 5, 'request_text': ''}
    assert check_lead(lead, [new, old], fresh_ids={'n1'}).status == 'held_stale'


def wire(data):
    return {'status': 'completed', 'usage': {'input_tokens': 900, 'output_tokens': 300},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(data), 'annotations': []}]}]}


class Retriever:
    def __init__(self, pages):
        self.pages, self.fetched = pages, []

    def fetch(self, url, *, reserve_fetches=0):
        from dochunt.retrieval import Retrieved
        self.fetched.append(url)
        r = self.pages.get(url)
        if r is None:
            return Retrieved(url, None, 'unavailable')
        text = ' '.join(s['text'] for s in r['sections'])
        return Retrieved(r['url'], text, 'Public document retrieved', '2026-10-06T00:00:00Z', hashlib.sha256(text.encode()).hexdigest(),
                         r['format'], r['title'], r['sections'], r['links'])


AUDIT = ('Issued Date October 02, 2026. The inspector general found that 12 of 40 sampled Example Agency contracts '
         'lacked required approvals. The agency agreed to review its contract files by March 2027.')
FOIA = ('Example Agency FOIA. FOIA requests for Example Agency records may be submitted by email to foia@example.gov '
        'or through the online portal.')


def watch_config(tmp_path):
    config = load_config(ROOT / 'newsroom.toml')
    config.editorial.geography = 'United States'
    config.hunt.mode = 'watch'
    config.hunt.watch = ['https://example.gov/feed']
    config.hunt.request_pages = ['https://example.gov/foia']
    config.hunt.keywords = []
    config.hunt.seen_path = str(tmp_path / 'seen.json')
    return config


def watch_pages():
    feed = RSS.replace(b'https://example.gov/reports/new.html', b'https://example.gov/reports/audit.html')
    return {'https://example.gov/feed': {**feed_receipt('https://example.gov/feed', feed), 'title': 'Reports'},
            'https://example.gov/reports/audit.html': receipt('https://example.gov/reports/audit.html', AUDIT),
            'https://example.gov/foia': receipt('https://example.gov/foia', FOIA)}


LEAD = {'headline': 'Did Example Agency fix the contracts that lacked approvals?',
        'established': [{'text': 'Auditors found 12 of 40 sampled Example Agency contracts lacked required approvals.', 'source': 'n1',
                         'quote': 'The inspector general found that 12 of 40 sampled Example Agency contracts lacked required approvals.'}],
        'qualifications': [{'text': 'The agency agreed to review its contract files by March 2027.', 'source': 'n1',
                            'quote': 'The agency agreed to review its contract files by March 2027.'}],
        'hypothesis': 'Some of the 12 contracts may remain unapproved.', 'already_answered': '',
        'records': [{'description': 'Approval records for the 12 sampled contracts', 'could_reveal': 'whether approvals were obtained',
                     'essential': True, 'kind': 'underlying', 'route': 'records_request', 'custodian': 'Example Agency',
                     'location': 'foia@example.gov', 'how': 'Email the request.', 'source': 'r0',
                     'quote': 'FOIA requests for Example Agency records may be submitted by email to foia@example.gov or through the online portal.'}],
        'request_text': 'Under the FOIA, I request copies of existing records: approval records for the 12 contracts sampled in the inspector general audit issued October 2, 2026.',
        'newsworthiness': 6}


def test_watch_mode_end_to_end_reports_the_new_document_and_remembers_it(tmp_path):
    config = watch_config(tmp_path)
    transport = FixtureTransport([wire({'leads': [LEAD], 'no_lead_reason': ''}),
                                  wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(watch_pages())
    issue, result = hunt_preview(config, provider, tmp_path / 'one', local_date=TODAY, record_seen=True)
    assert len(issue.leads) == 1
    html = (tmp_path / 'one' / 'preview.html').read_text()
    assert 'New document' in html and 'October 2, 2026' in html
    saved = json.loads((tmp_path / 'one' / 'sources.json').read_text())
    assert {d['id'] for d in saved} >= {'n1', 'r0'} and saved[0]['text']
    assert 'https://example.gov/reports/audit.html' in load_seen(config.hunt.seen_path)
    # Next run: nothing new, so no model call and an honest no-lead email.
    provider2 = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([]))
    provider2.retriever = Retriever(watch_pages())
    issue2, _ = hunt_preview(config, provider2, tmp_path / 'two', local_date=TODAY)
    assert issue2.leads == [] and any('0 new document' in n for n in issue2.coverage_results)


def test_watch_mode_with_nothing_to_watch_fails_before_any_fetch_or_call(tmp_path):
    config = watch_config(tmp_path)
    config.hunt.watch = []
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([]))
    provider.retriever = Retriever(watch_pages())
    with pytest.raises(ValueError, match='pages to watch'):
        hunt_preview(config, provider, tmp_path / 'out', local_date=TODAY)
    assert provider.retriever.fetched == [] and provider.transport.requests == []
    assert not (tmp_path / 'out' / 'issue.json').exists()


def test_watch_preview_without_record_seen_does_not_change_state(tmp_path):
    config = watch_config(tmp_path)
    transport = FixtureTransport([wire({'leads': [], 'no_lead_reason': 'Nothing worth requesting.'})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(watch_pages())
    hunt_preview(config, provider, tmp_path / 'p', local_date=TODAY)
    assert load_seen(config.hunt.seen_path) == {}


# Regressions from the independent review of 2f806c3: old documents must never look new.

@pytest.mark.parametrize('text', [
    'Revised Date: October 1, 2026 Original report March 2019',
    'The Army must finish repairs by October 1, 2026 and the report was Issued March 2021',
    'Last updated October 3, 2026. Report released June 2, 2023.',
    'Last reviewed October 4, 2026 | Report: Barracks mold, Fiscal Year 2020',
    'The hearing is scheduled for October 2, 2026. This 2019 report on barracks found mold.',
    'Audit Report DODIG-2021-055. Fiscal Year 2026 plan: work begins October 1, 2026.',
    'Posted October 3, 2026. The inspection occurred in 2019.',
    'Report Date: see cover. Page reviewed October 3, 2026.',
    'Published by the Inspector General; accessed October 3, 2026.',
    'Released: n/a - printed on October 3, 2026 from the archive.',
    'Issued: unknown. Last modified October 3, 2026.',
    'Page last changed Oct. 3, 2026; October 3, 2026',
])
def test_revision_deadline_and_update_dates_are_not_publication_dates(text):
    dated = document_date(text)
    assert dated is None or not within(dated.value, TODAY, 14)


def test_atom_published_beats_updated():
    atom = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><entry><title>Old audit</title>'
            b'<updated>2026-10-05T00:00:00Z</updated><published>2019-03-01T00:00:00Z</published>'
            b'<link href="https://example.gov/reports/old.html"/></entry></feed>')
    parsed = extract(atom, 'application/atom+xml', 'https://example.gov/atom', 5)
    assert parsed['sections'][0]['text'].startswith('2019-03-01')


def test_relisted_old_report_keeps_its_own_older_date():
    feed = RSS.replace(b'https://example.gov/reports/new.html', b'https://example.gov/reports/relisted.html')
    pages = {'https://example.gov/feed': feed_receipt('https://example.gov/feed', feed),
             'https://example.gov/reports/relisted.html': receipt('https://example.gov/reports/relisted.html',
                                                                   'DATE ISSUED: MARCH 4, 2019. Findings from the original audit.')}
    report = collect_new(pages.get, ['https://example.gov/feed'], today=TODAY)
    assert report.new == [] and any('2019-03-04' in s['reason'] for s in report.skipped)


def test_utf16_feed_cannot_hide_a_doctype():
    evil = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "x">]><rss><channel></channel></rss>'.encode('utf-16')
    assert extract(evil, 'application/rss+xml', 'https://example.gov/feed', 5) is None


def test_long_new_document_does_not_crowd_out_request_pages(tmp_path):
    config = watch_config(tmp_path)
    config.hunt.max_source_chars = 20000
    pages = watch_pages()
    long_audit = AUDIT + ' Background. ' * 4000
    pages['https://example.gov/reports/audit.html'] = receipt('https://example.gov/reports/audit.html', long_audit)
    # A realistic request page is several thousand characters, not a single sentence.
    pages['https://example.gov/foia'] = receipt('https://example.gov/foia', FOIA + ' Fee schedule and processing guidance.' * 120)
    transport = FixtureTransport([wire({'leads': [LEAD], 'no_lead_reason': ''}),
                                  wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, _ = hunt_preview(config, provider, tmp_path / 'long', local_date=TODAY)
    sent = transport.requests[0]['input']
    assert 'foia@example.gov' in sent and 'omitted by DocHunt' in sent
    assert len(issue.leads) == 1


def test_one_failed_document_keeps_earlier_results(tmp_path):
    config = watch_config(tmp_path)
    pages = watch_pages()
    second = RSS.replace(b'https://example.gov/reports/new.html', b'https://example.gov/reports/audit.html').replace(
        b'https://example.gov/reports/old.html', b'https://example.gov/reports/audit2.html').replace(
        b'Mon, 03 Mar 2025', b'Thu, 01 Oct 2026')
    pages['https://example.gov/feed'] = {**feed_receipt('https://example.gov/feed', second), 'title': 'Reports'}
    pages['https://example.gov/reports/audit2.html'] = receipt('https://example.gov/reports/audit2.html', AUDIT)
    from dochunt.providers.base import ProviderError
    transport = FixtureTransport([wire({'leads': [LEAD], 'no_lead_reason': ''}),
                                  wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]}),
                                  ProviderError('Provider HTTP 500')])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits(transient_retries=0)), transport)
    provider.retriever = Retriever(pages)
    issue, result = hunt_preview(config, provider, tmp_path / 'fail', local_date=TODAY)
    assert len(issue.leads) == 1 and any(h['status'] == 'run_error' for h in result.held)


def test_seen_state_records_listed_and_final_urls(tmp_path):
    config = watch_config(tmp_path)
    pages = watch_pages()
    final = receipt('https://example.gov/reports/audit-final.html', AUDIT)
    pages['https://example.gov/reports/audit.html'] = final  # fetch lands on a redirected URL
    transport = FixtureTransport([wire({'leads': [], 'no_lead_reason': 'none'})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    hunt_preview(config, provider, tmp_path / 's', local_date=TODAY, record_seen=True)
    seen = load_seen(config.hunt.seen_path)
    assert {'https://example.gov/reports/audit.html', 'https://example.gov/reports/audit-final.html'} <= set(seen)


def test_seen_state_skips_documents_left_unanalysed_by_a_failure(tmp_path):
    config = watch_config(tmp_path)
    pages = watch_pages()
    feed = RSS.replace(b'https://example.gov/reports/new.html', b'https://example.gov/reports/audit.html').replace(
        b'https://example.gov/reports/old.html', b'https://example.gov/reports/audit2.html').replace(
        b'Mon, 03 Mar 2025', b'Thu, 01 Oct 2026')
    pages['https://example.gov/feed'] = {**feed_receipt('https://example.gov/feed', feed), 'title': 'Reports'}
    pages['https://example.gov/reports/audit2.html'] = receipt('https://example.gov/reports/audit2.html', AUDIT)
    from dochunt.providers.base import ProviderError
    transport = FixtureTransport([ProviderError('Provider HTTP 500')])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits(transient_retries=0)), transport)
    provider.retriever = Retriever(pages)
    from dochunt.providers.base import ProviderError as _PE
    with pytest.raises(_PE):  # nothing analysed: a run failure, not a no-lead email
        hunt_preview(config, provider, tmp_path / 'f', local_date=TODAY, record_seen=True)
    assert load_seen(config.hunt.seen_path) == {}


def test_month_only_date_is_first_of_month_and_approximate():
    dated = document_date('Report to Congress September 2026. Findings follow.')
    assert dated.value == date(2026, 9, 1) and dated.approximate
    assert document_date('Report to Congress September 2026. Data cover fiscal year 2024.') is None


def test_label_filler_cannot_skip_over_an_older_year():
    dated = document_date('Released in March 2019 and revised October 1, 2026.')
    assert dated is None or not within(dated.value, TODAY, 14)


def test_unreadable_pages_report_why_and_request_pages_skip_failures(tmp_path):
    config = watch_config(tmp_path)
    config.hunt.request_pages = ['https://example.gov/missing', 'https://example.gov/foia']
    pages = watch_pages()
    pages['https://example.gov/reports/audit.html'] = None  # blocked document
    transport = FixtureTransport([])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, _ = hunt_preview(config, provider, tmp_path / 'r', local_date=TODAY)
    assert any('document unreadable (unavailable)' in n for n in issue.coverage_results)
    assert transport.requests == []


def test_watch_mode_uses_the_firecrawl_fallback_when_enabled(tmp_path, monkeypatch):
    # Federal sites returned HTTP 403 to the plain retriever in the first live watch run.
    # With the newsroom's fallback enabled, a blocked listing page is fetched through it.
    from dochunt.retrieval import FetchResponse, PublicRetriever
    monkeypatch.setenv('FIRECRAWL_API_KEY', 'mock-firecrawl-key')
    config = watch_config(tmp_path)
    config.retrieval.firecrawl_enabled = True
    config.hunt.watch = ['https://agency.example/reports/']
    config.hunt.request_pages = []
    budget = Budget(Limits())
    blocked = PublicRetriever(budget, resolver=lambda host: ['93.184.216.34'], connector=lambda *a: FetchResponse(403, {}, b''))
    scraped = []

    def scrape(endpoint, headers, payload, timeout, cap):
        scraped.append(payload['url'])
        return {'success': True, 'data': {'rawHtml': '<main><p>Reports</p></main>',
                'metadata': {'sourceURL': payload['url'], 'statusCode': 200, 'contentType': 'text/html'}}}
    provider = OpenAIProvider('gpt-6.1-sol', budget, FixtureTransport([]))
    provider.retriever, provider.firecrawl_transport = blocked, scrape
    issue, _ = hunt_preview(config, provider, tmp_path / 'fc', local_date=TODAY)
    assert scraped == ['https://agency.example/reports/']
    assert any('0 new document' in n for n in issue.coverage_results)


def test_uncertain_paid_fallback_usage_stops_the_run():
    from dochunt.budget import BudgetExceeded
    from dochunt.watch import retriever_fetch

    class Blocked:
        budget = Budget(Limits())

        def fetch(self, url):
            self.budget.blocked = True
            raise BudgetExceeded('Firecrawl cancellation uncertain')
    with pytest.raises(BudgetExceeded):
        retriever_fetch(Blocked())('https://agency.example/reports/')


def test_feed_items_off_the_beat_are_skipped_before_fetching():
    fetched = []
    pages = {'https://example.gov/feed': feed_receipt('https://example.gov/feed', RSS)}

    def fetch(url):
        fetched.append(url)
        return pages.get(url)
    report = collect_new(fetch, ['https://example.gov/feed'], today=TODAY, keywords=['military', 'Navy'])
    assert fetched == ['https://example.gov/feed']  # 'New audit' is off-topic: never opened
    assert any(s['reason'] == 'off-topic by title' for s in report.skipped)


# Regressions from live watch test W3 (2026-10-06).

def test_new_documents_publication_date_counts_as_evidence_for_numbers():
    new = _doc('n1', 'The Navy said dewatering is the most dangerous part of the dry dock project.', '2026-10-06')
    fact = {'text': "The Navy's October 6, 2026 update calls dewatering the most dangerous phase.", 'source': 'n1',
            'quote': 'The Navy said dewatering is the most dangerous part of the dry dock project.'}
    lead = {'headline': 'Is the dry dock safe to drain?', 'established': [fact], 'qualifications': [], 'hypothesis': 'h',
            'already_answered': '', 'records': [], 'newsworthiness': 5, 'request_text': ''}
    assert check_lead(lead, [new], fresh_ids={'n1'}).status != 'unsupported'


def test_hotline_mentioned_elsewhere_does_not_disqualify_a_linked_request_route():
    page = source_from_receipt(receipt('https://agency.example/foia',
        'Agency FOIA. Veterans Health Administration Securely request awarded contracts and records. Submit a VHA FOIA Request. '
        'Other FOIA categories include White House Hotline (WHH) Records.', links=['https://agency.example/portal']), 'r0')
    new = _doc('n1', 'Auditors found 12 of 40 sampled Veterans Health Administration contracts lacked approvals.', '2026-10-02')
    fact = {'text': 'Auditors found 12 of 40 sampled contracts lacked approvals.', 'source': 'n1',
            'quote': 'Auditors found 12 of 40 sampled Veterans Health Administration contracts lacked approvals.'}
    record = {'description': 'Approval records for sampled contracts', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Veterans Health Administration', 'location': 'https://agency.example/portal',
              'how': 'Use the portal.', 'source': 'r0', 'quote': 'Veterans Health Administration Securely request awarded contracts and records.'}
    lead = {'headline': 'Did the agency fix contracts that lacked approvals?', 'established': [fact], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 5,
            'request_text': 'Under the FOIA, I request copies of existing records: approval records for the 40 sampled contracts, 2025-2026.'}
    checked = check_lead(lead, [new, page], fresh_ids={'n1'})
    assert checked.status == 'ready', checked.problems


# Nits from the review of bb987b3.

def test_any_link_on_a_foia_page_is_not_a_request_route():
    page = source_from_receipt(receipt('https://agency.example/foia',
        'Agency FOIA. Veterans Health Administration Securely request awarded contracts and records. Submit a VHA FOIA Request.',
        links=['https://agency.example/accessibility', 'https://agency.example/portal']), 'r0')
    new = _doc('n1', 'Auditors found 12 of 40 sampled Veterans Health Administration contracts lacked approvals.', '2026-10-02')
    fact = {'text': 'Auditors found 12 of 40 sampled contracts lacked approvals.', 'source': 'n1',
            'quote': 'Auditors found 12 of 40 sampled Veterans Health Administration contracts lacked approvals.'}
    record = {'description': 'Approval records for sampled contracts', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Veterans Health Administration', 'location': 'https://agency.example/accessibility',
              'how': 'Use the page.', 'source': 'r0', 'quote': 'Veterans Health Administration Securely request awarded contracts and records.'}
    lead = {'headline': 'Did the agency fix contracts that lacked approvals?', 'established': [fact], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 5,
            'request_text': 'Under the FOIA, I request copies of existing records: approval records for the 40 sampled contracts, 2025-2026.'}
    assert check_lead(lead, [new, page], fresh_ids={'n1'}).status == 'held_access'


def test_feed_timestamp_numbers_do_not_support_a_fact():
    new = _doc('n1', 'The Navy described the dry dock project.', '2026-10-02')
    new.published_evidence = 'feed item dated Thu, 02 Oct 2026 14:30:00 GMT'
    fact = {'text': 'The Navy reported 14 injuries at the dry dock.', 'source': 'n1', 'quote': 'The Navy described the dry dock project.'}
    lead = {'headline': 'Is the dry dock safe?', 'established': [fact], 'qualifications': [], 'hypothesis': 'h',
            'already_answered': '', 'records': [], 'newsworthiness': 5, 'request_text': ''}
    assert check_lead(lead, [new], fresh_ids={'n1'}).status == 'unsupported'


# Two-section email: leads whose route the checks could not confirm (owner decision 2026-10-06).

QUESTIONS_PAGE = ('Example Agency Records Office. If you have questions about Example Agency records, '
                  'contact the Records Office at help@example.gov.')


def unconfirmed_lead():
    record = dict(LEAD['records'][0], location='help@example.gov', source='r0',
                  quote='If you have questions about Example Agency records, contact the Records Office at help@example.gov.')
    return dict(LEAD, records=[record])


def two_tier_run(tmp_path, verdicts, leads):
    config = watch_config(tmp_path)
    pages = watch_pages()
    pages['https://example.gov/foia'] = receipt('https://example.gov/foia', QUESTIONS_PAGE)
    transport = FixtureTransport([wire({'leads': leads, 'no_lead_reason': ''}), wire({'verdicts': verdicts})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, result = hunt_preview(config, provider, tmp_path / 'tier', local_date=TODAY)
    return issue, result, transport


def test_unconfirmed_route_is_reviewed_with_a_flag_and_shown_under_check_the_route(tmp_path):
    issue, _, transport = two_tier_run(tmp_path, [{'lead': 'L1', 'verdict': 'approve', 'problems': []}], [unconfirmed_lead()])
    review = json.loads(transport.requests[1]['input'].split('\n', 1)[1])
    assert review['leads'][0]['route_unverified'] is True
    assert len(issue.leads) == 1 and issue.leads[0].records_plan['tier'] == 'verify_route'
    html = (tmp_path / 'tier' / 'preview.html').read_text()
    assert 'Check the request route first' in html and 'To confirm' in html and 'Route to confirm' in html


def test_reviewer_can_still_reject_an_unconfirmed_route(tmp_path):
    issue, result, _ = two_tier_run(tmp_path, [{'lead': 'L1', 'verdict': 'reject', 'problems': ['wrong custodian']}],
                                    [unconfirmed_lead()])
    assert issue.leads == [] and result.held[-1]['status'] == 'rejected_by_review'


def test_confirmed_leads_come_before_unconfirmed_ones(tmp_path):
    config = watch_config(tmp_path)
    config.hunt.request_pages = ['https://example.gov/foia', 'https://example.gov/questions']
    pages = watch_pages()  # r0: FOIA page with a submission sentence
    pages['https://example.gov/questions'] = receipt('https://example.gov/questions', QUESTIONS_PAGE)  # r1
    unconfirmed = dict(unconfirmed_lead(), headline='Did Example Agency fix contracts lacking approvals?',
                       records=[dict(unconfirmed_lead()['records'][0], source='r1')])
    confirmed = LEAD  # cites r0, where submission is stated
    transport = FixtureTransport([wire({'leads': [unconfirmed, confirmed], 'no_lead_reason': ''}),
                                  wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []},
                                                     {'lead': 'L2', 'verdict': 'approve', 'problems': []}]})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, _ = hunt_preview(config, provider, tmp_path / 'order', local_date=TODAY)
    assert [l.records_plan['tier'] for l in issue.leads] == ['ready', 'verify_route']


# Regressions from the review of 5330c6f: hard holds must never become "check the route".

def _single(tmp_path, lead):
    config = watch_config(tmp_path)
    pages = watch_pages()
    pages['https://example.gov/foia'] = receipt('https://example.gov/foia', QUESTIONS_PAGE)
    transport = FixtureTransport([wire({'leads': [lead], 'no_lead_reason': ''}),
                                  wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, result = hunt_preview(config, provider, tmp_path / 'x', local_date=TODAY)
    return issue, result, transport


@pytest.mark.parametrize('change', [
    # the original false positive: the audit itself as the "route" for underlying records
    {'route': 'public_download', 'location': 'https://example.gov/reports/audit.html', 'source': 'n1',
     'quote': 'The inspector general found that 12 of 40 sampled Example Agency contracts lacked required approvals.'},
    # fabricated route quote
    {'quote': 'Requests may be sent to the Records Office at help@example.gov by email.'},
    # guessed location not in any source
    {'location': 'Example Agency Records Portal'},
])
def test_hard_route_failures_are_never_reviewed_or_shown(tmp_path, change):
    record = dict(unconfirmed_lead()['records'][0], **change)
    issue, result, transport = _single(tmp_path, dict(unconfirmed_lead(), records=[record]))
    assert issue.leads == [] and len(transport.requests) == 1  # no review call


def test_bad_request_language_is_never_shown_even_with_an_unconfirmed_route(tmp_path):
    bad = dict(unconfirmed_lead(), request_text='I request [insert audit findings] records. ' + 'x' * 2000)
    issue, _, transport = _single(tmp_path, bad)
    assert issue.leads == [] and len(transport.requests) == 1


def test_presentation_failures_hold_unconfirmed_route_leads(tmp_path):
    bad = dict(unconfirmed_lead(), headline='Did the EXA agency fix the contracts that lacked required approvals last year after audit?')
    issue, result, transport = _single(tmp_path, bad)
    assert issue.leads == [] and result.held[0]['status'] == 'held_presentation'


def test_unconfirmed_location_is_never_a_clickable_link(tmp_path):
    record = dict(unconfirmed_lead()['records'][0], location='https://example.gov/questions-form')
    page = QUESTIONS_PAGE + ' Form: https://example.gov/questions-form'
    config = watch_config(tmp_path)
    pages = watch_pages()
    pages['https://example.gov/foia'] = receipt('https://example.gov/foia', page)
    transport = FixtureTransport([wire({'leads': [dict(unconfirmed_lead(), records=[record])], 'no_lead_reason': ''}),
                                  wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, _ = hunt_preview(config, provider, tmp_path / 'link', local_date=TODAY)
    html = (tmp_path / 'link' / 'preview.html').read_text()
    assert issue.leads and issue.leads[0].records_plan['tier'] == 'verify_route'
    assert 'href="https://example.gov/questions-form"' not in html


def test_ready_leads_lead_the_email_across_documents(tmp_path):
    config = watch_config(tmp_path)
    config.hunt.request_pages = ['https://example.gov/foia', 'https://example.gov/questions']
    pages = watch_pages()
    pages['https://example.gov/questions'] = receipt('https://example.gov/questions', QUESTIONS_PAGE)
    feed = RSS.replace(b'https://example.gov/reports/new.html', b'https://example.gov/reports/audit.html').replace(
        b'https://example.gov/reports/old.html', b'https://example.gov/reports/audit2.html').replace(b'Mon, 03 Mar 2025', b'Thu, 01 Oct 2026')
    pages['https://example.gov/feed'] = {**feed_receipt('https://example.gov/feed', feed), 'title': 'Reports'}
    pages['https://example.gov/reports/audit2.html'] = receipt('https://example.gov/reports/audit2.html', AUDIT)
    unconfirmed = dict(unconfirmed_lead(), records=[dict(unconfirmed_lead()['records'][0], source='r1')])
    approve = wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})
    # Newest document first (audit.html, Oct 2) yields an unconfirmed lead; the next a ready one.
    second = dict(LEAD, established=[dict(f, source='n2') for f in LEAD['established']],
                  qualifications=[dict(f, source='n2') for f in LEAD['qualifications']])
    transport = FixtureTransport([wire({'leads': [unconfirmed], 'no_lead_reason': ''}), approve,
                                  wire({'leads': [second], 'no_lead_reason': ''}), approve])
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport)
    provider.retriever = Retriever(pages)
    issue, _ = hunt_preview(config, provider, tmp_path / 'multi', local_date=TODAY)
    assert [l.records_plan['tier'] for l in issue.leads] == ['ready', 'verify_route']


def test_another_offices_address_and_hotlines_are_wrong_routes_not_unconfirmed():
    from dochunt.engine import UNCONFIRMED_ROUTE
    page = source_from_receipt(json.loads((ROOT / 'eval/sources/la-dph-prr-policy.json').read_text()), 'r0')
    new = _doc('n1', 'The Department of Public Health reported 40 hazardous materials inspections at the landfill.', '2026-10-02')
    fact = {'text': 'Public Health reported 40 hazardous materials inspections.', 'source': 'n1',
            'quote': 'The Department of Public Health reported 40 hazardous materials inspections at the landfill.'}
    record = {'description': 'Hazardous materials inspection files', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Los Angeles County Department of Public Health',
              'location': 'https://fire.lacounty.gov/public-records-requests/', 'how': 'Use the portal.', 'source': 'r0',
              'quote': page.text[page.text.find('The Custodian of Records coordinates'):][:100].rsplit(' ', 2)[0]}
    lead = {'headline': 'Were the landfill hazardous materials inspections complete?', 'established': [fact], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 5,
            'request_text': 'I request copies of existing records: hazardous materials inspection files for 2026, under the CPRA.'}
    checked = check_lead(lead, [new, page], fresh_ids={'n1'})
    assert checked.status == 'held_access' and not all(p.startswith(UNCONFIRMED_ROUTE) for p in checked.problems)
    hotline = source_from_receipt(receipt('https://agency.example/hotline',
        'Example Agency Inspector General. To report fraud, file a complaint with the Hotline at hotline@agency.example.'), 'r1')
    record2 = dict(record, custodian='Example Agency Inspector General', location='hotline@agency.example', source='r1',
                   quote='To report fraud, file a complaint with the Hotline at hotline@agency.example.')
    checked2 = check_lead(dict(lead, records=[record2]), [new, hotline], fresh_ids={'n1'})
    assert checked2.status == 'held_access' and any(p.startswith('address is a complaint or hotline channel') for p in checked2.problems)


# Scheduled delivery path (engine-only release): seen documents persist in the newsroom's
# own state, recorded only once an email is accepted.

def test_scheduled_watch_run_records_seen_documents_only_after_acceptance(tmp_path):
    from datetime import datetime, timezone
    from dochunt.runtime import run
    from dochunt.state import StateStore
    from test_delivery import Fake
    config = watch_config(tmp_path)
    store = StateStore(tmp_path / 'data', git=False)
    approve = wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([wire({'leads': [LEAD], 'no_lead_reason': ''}), approve]))
    provider.retriever = Retriever(watch_pages())
    mail = Fake()
    receipt = run(config, store, tmp_path / 'out1', provider=provider, transport=mail, manual=True,
                  now_utc=datetime(2026, 10, 6, 14, tzinfo=timezone.utc))
    assert receipt.state.value == 'accepted' and len(mail.calls) == 1
    assert mail.calls[0].issue['leads'][0]['provisional_headline'] == LEAD['headline']
    assert 'https://example.gov/reports/audit.html' in store.read()['seen_documents']
    # Next day: the same document is not new any more, so no model call is made.
    quiet = FixtureTransport([])
    provider2 = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), quiet)
    provider2.retriever = Retriever(watch_pages())
    run(config, store, tmp_path / 'out2', provider=provider2, transport=Fake(), manual=True,
        now_utc=datetime(2026, 10, 7, 14, tzinfo=timezone.utc))
    assert quiet.requests == []


def test_failed_send_does_not_mark_documents_seen(tmp_path):
    from datetime import datetime, timezone
    from dochunt.runtime import run
    from dochunt.state import StateStore
    from dochunt.delivery import DefiniteNonSend

    class Down:
        def send(self, p):
            raise DefiniteNonSend('provider rejected before handoff')
    config = watch_config(tmp_path)
    store = StateStore(tmp_path / 'data', git=False)
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([wire({'leads': [], 'no_lead_reason': 'none'})]))
    provider.retriever = Retriever(watch_pages())
    with pytest.raises(Exception):
        run(config, store, tmp_path / 'out', provider=provider, transport=Down(), manual=True,
            now_utc=datetime(2026, 10, 6, 14, tzinfo=timezone.utc))
    assert not store.read().get('seen_documents')


def test_engine_preview_artifacts_reload_for_a_test_send(tmp_path):
    from dochunt.artifacts import load_preview
    from dochunt.cli import preview
    config = watch_config(tmp_path)
    approve = wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([wire({'leads': [LEAD], 'no_lead_reason': ''}), approve]))
    provider.retriever = Retriever(watch_pages())
    preview(config, tmp_path / 'p', provider=provider, local_date=TODAY)
    issue, rendered = load_preview(tmp_path / 'p', config, require_paid=True, require_send=True)
    assert issue.leads[0].provisional_headline == LEAD['headline'] and (tmp_path / 'p' / 'raw').is_dir()
    # Tampering with a saved source breaks the literal-evidence check.
    sources = json.loads((tmp_path / 'p' / 'sources.json').read_text())
    sources[0]['text'] = 'altered'
    (tmp_path / 'p' / 'sources.json').write_text(json.dumps(sources))
    with pytest.raises(ValueError):
        load_preview(tmp_path / 'p', config, require_paid=True)


def test_seen_documents_are_pruned_after_180_days(tmp_path):
    from dataclasses import replace
    from dochunt.delivery import send
    from dochunt.state import StateStore
    from test_delivery import Fake, prepared
    store = StateStore(tmp_path / 'data', git=False)
    store.save({'attempts': {}, 'completed': {}, 'history': [], 'seen_documents': {'https://old.example/a': '2026-01-01'}})
    p = replace(prepared(), seen_documents=('https://new.example/b',))
    send(p, store, Fake())
    seen = store.read()['seen_documents']
    assert 'https://new.example/b' in seen and 'https://old.example/a' not in seen


def test_cli_deliver_from_engine_artifacts_records_seen_documents(tmp_path, monkeypatch):
    from dochunt import cli
    from dochunt.cli import preview
    from dochunt.state import StateStore
    from test_delivery import Fake
    config = watch_config(tmp_path)
    approve = wire({'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]})
    provider = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([wire({'leads': [LEAD], 'no_lead_reason': ''}), approve]))
    provider.retriever = Retriever(watch_pages())
    preview(config, tmp_path / 'p', provider=provider, local_date=TODAY)
    from dochunt.artifacts import load_preview
    from dochunt.delivery import prepare, send
    issue, rendered = load_preview(tmp_path / 'p', config, require_paid=True, require_send=True)
    seen = tuple(json.loads((tmp_path / 'p' / 'analysed-documents.json').read_text()))
    store = StateStore(tmp_path / 'data', git=False)
    send(prepare(issue, rendered, config, seen_documents=seen), store, Fake())
    assert 'https://example.gov/reports/audit.html' in store.read()['seen_documents']
