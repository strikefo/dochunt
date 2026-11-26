from datetime import date
import json
from importlib.resources import files
import pytest
from support import sample_data
from dochunt.models import (IssueFormatError, SendReceipt, issue_to_data, parse_issue_data, public_url, source_key)

D = date(2026, 10, 2)


def parse(data):
    return parse_issue_data(data, issue_key='x:2026-10-02', local_date=D)


def test_fixture_round_trips_through_the_agent_schema_shape():
    data = sample_data()
    data.pop('checked_sources')  # research record, kept in the check report rather than the issue
    assert issue_to_data(parse(data)) == data


def test_schema_file_matches_the_parser_fields():
    schema = json.loads(files('dochunt').joinpath('schema', 'issue.schema.json').read_text())
    lead = schema['properties']['leads']['items']
    assert set(schema['required']) == set(sample_data()) == {'subject', 'intro', 'no_lead_reason', 'checked_sources', 'leads'}
    assert set(lead['required']) == set(sample_data()['leads'][0])
    records = lead['properties']['get_records']
    assert records['properties']['action']['enum'] == ['foia', 'inquiry', 'download']
    # Strict structured-output schemas: every object closed, every property required.
    def closed(node):
        if node.get('type') == 'object':
            assert node['additionalProperties'] is False and set(node['required']) == set(node['properties'])
            for child in node['properties'].values():
                closed(child)
        if node.get('type') == 'array':
            closed(node['items'])
    closed(schema)


def test_every_structural_problem_is_reported_at_once():
    data = sample_data()
    data['leads'][0]['internal']['newsworthiness'] = 11
    data['leads'][1]['get_records']['action'] = 'subpoena'
    del data['leads'][2]['caveat']
    data['subject'] = 'Two\nlines'
    with pytest.raises(IssueFormatError) as error:
        parse(data)
    text = ' | '.join(error.value.problems)
    assert 'lead 1.internal.newsworthiness' in text and 'lead 2.get_records.action' in text
    assert 'lead 3 is missing fields: caveat' in text and 'subject must be one line' in text


@pytest.mark.parametrize('change,message', [
    (lambda l: l['source'].update(url='javascript:alert(1)'), 'source link'),
    (lambda l: l['get_records']['contact'].update(url='', email=''), 'submission link or email'),
    (lambda l: l['get_records'].update(verified_at_url=''), 'verified'),
    (lambda l: l['get_records']['contact'].update(email='not-an-email'), 'email'),
])
def test_reader_facing_lead_rules(change, message):
    data = sample_data()
    change(data['leads'][0])
    with pytest.raises(IssueFormatError, match=message):
        parse(data)


def test_download_needs_a_direct_link_but_no_verification_page():
    data = sample_data()
    parse(data)  # lead 3 is a download with no verified_at_url
    data['leads'][2]['get_records']['contact']['url'] = ''
    with pytest.raises(IssueFormatError, match='download needs a direct link'):
        parse(data)


def test_no_lead_issue_needs_a_reason_and_leads_need_none():
    empty = sample_data('no-leads')
    assert parse(empty).leads == []
    empty['no_lead_reason'] = ''
    with pytest.raises(IssueFormatError, match='no-lead reason'):
        parse(empty)
    full = sample_data()
    full['no_lead_reason'] = 'contradiction'
    with pytest.raises(IssueFormatError, match='no_lead_reason must be empty'):
        parse(full)


def test_source_key_ignores_tracking_case_and_trailing_slash():
    assert source_key('https://WWW.GAO.gov/products/gao-27-1/?utm_source=x&b=2&a=1#top') == source_key('http://gao.gov/products/gao-27-1?a=1&b=2')
    assert source_key('https://gao.gov/a') != source_key('https://gao.gov/b')


def test_urls_without_credentials_or_whitespace():
    for bad in ('ftp://x.example', 'https://user:pw@x.example', 'https://x.example/a b', ''):
        with pytest.raises(ValueError):
            public_url(bad)


def test_receipt_distinguishes_acceptance_from_delivery():
    with pytest.raises(ValueError):
        SendReceipt('resend', 'accepted')
    assert SendReceipt('smtp', 'unknown').state.value == 'unknown'


def test_source_key_keeps_hash_routes_but_not_anchors():
    assert source_key('https://agency.gov/#/records/123') != source_key('https://agency.gov/#/records/456')
    assert source_key('https://agency.gov/#!/doc/9') != source_key('https://agency.gov/')
    assert source_key('https://agency.gov/report#section-2') == source_key('https://agency.gov/report')


def test_source_key_keeps_non_default_ports():
    assert source_key('https://records.example.gov:8443/r.pdf') != source_key('https://records.example.gov:9443/r.pdf')
    assert source_key('https://records.example.gov:443/r.pdf') == source_key('http://records.example.gov:80/r.pdf')
