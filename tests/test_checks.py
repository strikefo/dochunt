from datetime import date
import pytest
from support import sample_data
from dochunt.checks import assess, finalize, warnings_for
from dochunt.models import NO_LEAD_SUBJECT, source_key

D = date(2026, 10, 2)


def run(data, *, max_leads=3, sent=()):
    a = assess(data, max_leads=max_leads, sent=set(sent))
    issue, dropped = finalize(a, issue_key='x:2026-10-02', local_date=D, max_leads=max_leads)
    return a, issue, dropped


def test_clean_output_passes_unchanged():
    a, issue, dropped = run(sample_data())
    assert not a.problems and not dropped and len(issue.leads) == 3
    assert issue.subject == sample_data()['subject']


def test_a_broken_lead_is_dropped_and_the_rest_are_sent():
    data = sample_data()
    data['leads'][1]['get_records']['contact'] = {'label': '', 'url': '', 'email': '', 'phone': ''}
    a, issue, dropped = run(data)
    assert [l.headline for l in issue.leads] == [data['leads'][0]['headline'], data['leads'][2]['headline']]
    assert 'inquiry needs an email' in dropped[0]['problems'][0]
    assert issue.subject == data['subject']  # the top lead survived, so its subject stays


def test_dropping_the_top_lead_rewrites_the_subject_from_the_next_lead():
    data = sample_data()
    data['leads'][0]['source']['url'] = 'not a link'
    _, issue, _ = run(data)
    assert issue.subject == data['leads'][1]['headline']


def test_long_replacement_subject_is_trimmed_at_a_word():
    data = sample_data()
    data['leads'][0]['source']['url'] = 'not a link'
    data['leads'][1]['headline'] = 'Word ' * 25
    _, issue, _ = run(data)
    assert len(issue.subject) <= 100 and issue.subject.endswith('…')


def test_previously_sent_source_is_dropped_unless_marked_as_an_update():
    data = sample_data()
    sent = {source_key(data['leads'][0]['source']['url'])}
    a, issue, dropped = run(data, sent=sent)
    assert len(issue.leads) == 2 and 'already sent' in dropped[0]['problems'][0]
    data['leads'][0]['internal']['is_update'] = True
    _, issue, dropped = run(data, sent=sent)
    assert len(issue.leads) == 3 and not dropped


def test_two_leads_on_one_document_keep_only_the_first():
    data = sample_data()
    data['leads'][1]['source']['url'] = data['leads'][0]['source']['url'] + '/'
    _, issue, dropped = run(data)
    assert len(issue.leads) == 2 and 'repeats the source document' in dropped[0]['problems'][0]


def test_too_many_leads_keeps_the_agent_ranking():
    data = sample_data()
    a, issue, dropped = run(data, max_leads=2)
    assert 'keep only the strongest 2' in a.problems[0]
    assert [l.headline for l in issue.leads] == [l['headline'] for l in data['leads'][:2]] and len(dropped) == 1


def test_all_leads_failing_becomes_an_honest_no_lead_issue():
    data = sample_data()
    for lead in data['leads']:
        lead['source']['url'] = 'bad'
    _, issue, dropped = run(data)
    assert issue.leads == [] and issue.subject == NO_LEAD_SUBJECT and 'did not pass' in issue.no_lead_reason
    assert len(dropped) == 3


def test_non_object_output_cannot_produce_an_issue():
    import pytest
    from dochunt.models import IssueFormatError
    with pytest.raises(IssueFormatError):
        run(['not', 'an', 'object'])


def test_warnings_never_change_the_issue():
    data = sample_data()
    data['leads'][0]['headline'] = 'County paid $9.9 million early'
    _, issue, _ = run(data)
    statuses = {'https://auditor.example/reports/2026-07-road-repair-payments.pdf': 404}
    found = warnings_for(issue, lambda url: statuses.get(url, 403))
    assert any('9.9' in w for w in found)
    assert any('HTTP 404' in w for w in found)
    assert not any('HTTP 403' in w for w in found)  # bot blocking is normal on government sites
    assert len(issue.leads) == 3


def test_malformed_source_url_drops_only_that_lead():
    data = sample_data()
    data['leads'][0]['source']['url'] = 'https://[broken'
    a, issue, dropped = run(data)
    assert len(issue.leads) == 2 and 'source link' in dropped[0]['problems'][0]


def test_link_checks_stop_when_the_deadline_is_near():
    calls = []
    _, issue, _ = run(sample_data())
    found = warnings_for(issue, lambda url: calls.append(url) or 404, time_left=lambda: 50)
    assert not calls and any('skipped' in w for w in found)


@pytest.mark.parametrize('url', ['http://127.0.0.1:1/', 'http://169.254.169.254/latest/meta-data/', 'http://10.0.0.1/',
                                 'http://[::1]/', 'https://[broken', 'file:///etc/passwd', 'https://user:pw@example.org/'])
def test_link_checker_refuses_non_public_or_odd_destinations(url, monkeypatch):
    import socket
    from dochunt import checks
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('connected to ' + url))
    assert checks.http_status(url) is None


def test_link_checker_pins_the_checked_address_and_ignores_redirects(monkeypatch):
    import http.server
    import socket
    import threading
    from dochunt import checks

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302 if self.path == '/moved' else 404)
            self.send_header('Location', 'http://169.254.169.254/')
            self.end_headers()

        def log_message(self, *a):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    # Pretend the public name resolves to this test server; the connection must use that exact address.
    monkeypatch.setattr(checks, '_public_addresses', lambda host, p: [(socket.AF_INET, ('127.0.0.1', port))])
    try:
        assert checks.http_status(f'http://records.example.org:{port}/missing') == 404
        assert checks.http_status(f'http://records.example.org:{port}/moved') == 302
    finally:
        server.shutdown()


def test_a_stalled_link_probe_is_abandoned_at_its_wall_clock_limit():
    import time
    _, issue, _ = run(sample_data())
    start = time.monotonic()
    found = warnings_for(issue, lambda url: time.sleep(30) or 404, time_left=lambda: 91)
    assert time.monotonic() - start < 10 and not any('HTTP' in w for w in found)


def test_a_rejected_lead_does_not_block_a_valid_lead_on_the_same_document():
    data = sample_data()
    data['leads'][0]['get_records']['verified_at_url'] = ''
    data['leads'][1]['source']['url'] = data['leads'][0]['source']['url']
    _, issue, dropped = run(data)
    assert [l.headline for l in issue.leads] == [data['leads'][1]['headline'], data['leads'][2]['headline']]
    assert len(dropped) == 1 and 'verified' in dropped[0]['problems'][0]


def test_intro_is_cleared_when_any_lead_is_dropped():
    data = sample_data()
    assert run(data)[1].intro == data['intro']
    data['leads'][2]['get_records']['contact']['url'] = ''
    assert run(data)[1].intro == ''


def test_subject_drops_a_secondary_highlight_when_any_lead_is_dropped():
    data = sample_data()
    data['subject'] = 'County paid $1.2 million early; schools lack fire alarms'
    data['leads'][1]['get_records']['contact'] = {'label': '', 'url': '', 'email': '', 'phone': ''}
    assert run(data)[1].subject == 'County paid $1.2 million early'
    assert run(sample_data() | {'subject': 'A; B'})[1].subject == 'A; B'  # nothing dropped: unchanged


def test_a_subject_with_control_characters_is_replaced_by_the_top_headline():
    data = sample_data()
    data['subject'] = 'Bell\u0007 subject'
    a, issue, _ = run(data)
    assert 'control characters' in a.problems[0] and issue.subject == data['leads'][0]['headline']


def test_no_leads_without_shown_research_fails_instead_of_sending():
    from dochunt.models import IssueFormatError
    data = sample_data('no-leads')
    data['checked_sources'] = []
    data['no_lead_reason'] = 'Live-source research was blocked because the browsing tool was unavailable.'
    a = assess(data, max_leads=3, sent=set())
    assert any('checked_sources is empty' in p for p in a.problems)
    with pytest.raises(IssueFormatError, match='web access') as error:
        finalize(a, issue_key='x', local_date=D, max_leads=3)
    assert 'browsing tool was unavailable' in str(error.value)


def test_leads_still_send_when_the_source_list_is_thin():
    data = sample_data()
    data['checked_sources'] = ['https://auditor.example/reports/2026-07-road-repair-payments.pdf']
    a, issue, _ = run(data)
    assert len(issue.leads) == 3 and a.checked_sources == data['checked_sources']


def test_checked_sources_ignore_non_links_and_duplicates():
    data = sample_data()
    data['checked_sources'] = ['not a url', 'https://a.example/x', 'https://a.example/x', 'javascript:alert(1)']
    a = assess(data, max_leads=3, sent=set())
    assert a.checked_sources == ['https://a.example/x']


def test_too_few_sources_with_no_leads_asks_for_a_repair_first():
    data = sample_data('no-leads')
    data['checked_sources'] = data['checked_sources'][:2]
    a = assess(data, max_leads=3, sent=set())
    assert any('at least 3' in p for p in a.problems)


def test_anchor_and_tracking_variants_count_as_one_source():
    from dochunt.models import IssueFormatError
    data = sample_data('no-leads')
    data['checked_sources'] = ['https://a.example/r.pdf#page=1', 'https://a.example/r.pdf#page=2', 'https://a.example/r.pdf?utm_source=x']
    a = assess(data, max_leads=3, sent=set())
    assert a.checked_sources == ['https://a.example/r.pdf#page=1']
    with pytest.raises(IssueFormatError):
        finalize(a, issue_key='x', local_date=D, max_leads=3)
