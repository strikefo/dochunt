"""Evidence engine: record-level access gates, reject-only review, whole-source context.

Sources are the frozen saved receipts in eval/sources. Model outputs are authored
fixtures shaped like the failures seen in real provider runs; they are not provider
results and say nothing about editorial quality.
"""
from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest

from dochunt.budget import Budget
from dochunt.config import load_config, config_from_data
from dochunt.engine import (EngineError, analyse, check_lead, load_playbook, pack_sources,
                            presentation_problems, source_from_receipt, to_issue_records)
from dochunt.models import Issue, Limits
from dochunt.providers.base import FixtureTransport
from dochunt.providers.openai import OpenAIProvider
from dochunt.render import render_issue

ROOT = Path(__file__).resolve().parents[1]


def docs(*ids):
    return [source_from_receipt(json.loads((ROOT / 'eval/sources' / f'{i}.json').read_text()), i) for i in ids]


def wire(data):
    return {'status': 'completed', 'usage': {'input_tokens': 900, 'output_tokens': 300},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(data), 'annotations': []}]}]}


def provider(*outputs):
    transport = FixtureTransport([wire(o) for o in outputs])
    return OpenAIProvider('gpt-6.1-sol', Budget(Limits()), transport), transport


PROGRAM = {'text': 'The Mitchell-Lama program dates to 1955.', 'source': 'osc-mitchell-lama',
           'quote': 'The Mitchell-Lama Housing Program (Program) was created in 1955'}
AUDIT_PERIOD = {'text': 'The audit covered January 2019 through April 2025.', 'source': 'osc-mitchell-lama',
                'quote': 'The audit covered the period from January 2019 through April 2025.'}
CERTIFICATION = {'text': 'HPD began requiring annual compliance certifications in 2025.', 'source': 'hpd-response',
                 'quote': 'as of 2025, HPD is requiring managing agents to submit an annual compliance certification'}
DISAGREEMENT = {'text': 'HPD disagrees that it is not pursuing prompt corrective action.', 'source': 'hpd-response',
                'quote': 'HPD disagrees with the implication that, given the resources available, it is not pursuing prompt'}
FULL_AUDIT = {'description': 'The full audit report', 'could_reveal': 'specific locations', 'essential': True,
              'kind': 'seed', 'route': 'public_download', 'custodian': '',
              'location': 'https://www.osc.ny.gov/files/state-agencies/audits/pdf/sga-2026-24n4.pdf',
              'how': 'Download the report.', 'source': 'osc-mitchell-lama', 'quote': 'The audit covered the period from January 2019 through April 2025.'}
CERTIFICATIONS = {'description': '2025 certifications for the three developments', 'could_reveal': 'whether inspections happened',
                  'essential': True, 'kind': 'underlying', 'route': 'unknown', 'custodian': '', 'location': '', 'how': '', 'source': '', 'quote': ''}
VIOLATIONS = {'description': 'Open housing code violations for the three developments', 'could_reveal': 'whether audited hazards remain open',
              'essential': True, 'kind': 'underlying', 'route': 'public_download', 'custodian': 'HPD',
              'location': "HPD's Open Data portal", 'how': "Search HPD's Open Data portal for each building's violations.",
              'source': 'hpd-foil', 'quote': "much of which is available through HPD's Open Data portal, including: Multiple Dwelling Registration information from owners of residential units Housing Maintenance Code Complaints made by the public through 311 Citizen Services Center, Code Enforcement Borough Offices, and/or Online portals Open Housing Maintenance Code Violations issued by HPD"}


def lead(headline='Are hazards found at three Mitchell-Lama buildings still open?', records=(VIOLATIONS,), **extra):
    return {'headline': headline, 'established': [PROGRAM, AUDIT_PERIOD, CERTIFICATION], 'qualifications': [DISAGREEMENT],
            'hypothesis': 'Hazards cited by auditors may remain uncorrected.', 'already_answered': '',
            'records': list(records), 'request_text': '', 'newsworthiness': 7, **extra}


APPROVE = {'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': []}]}


def test_housing_false_positive_shape_is_held_for_access_without_review():
    # Observed 2026-10-06: a public link to the audit itself was accepted while the
    # reporting test needed certification records with no route.
    p, transport = provider({'leads': [lead(records=(FULL_AUDIT, CERTIFICATIONS))], 'no_lead_reason': ''})
    result = analyse(p, brief='NYC housing', geography='New York City',
                     docs=docs('osc-mitchell-lama', 'hpd-response', 'osc-dashboard'))
    assert result.approved == []
    assert result.held[0]['status'] == 'held_access'
    assert any('no supported access route' in x for x in result.held[0]['problems'])
    assert len(transport.requests) == 1  # no review, no correction call


def test_headline_rewrite_cannot_clear_an_access_blocker():
    narrowed = lead('What does the published audit say about three Mitchell-Lama buildings?',
                    records=(FULL_AUDIT, CERTIFICATIONS))
    assert check_lead(narrowed, docs('osc-mitchell-lama', 'hpd-response')).status == 'held_access'


def test_seed_only_hunt_is_not_a_lead():
    checked = check_lead(lead(records=(FULL_AUDIT,)), docs('osc-mitchell-lama', 'hpd-response'))
    assert checked.status == 'held_seed_only'


def test_public_data_route_from_custodian_page_passes_and_renders():
    p, transport = provider({'leads': [lead()], 'no_lead_reason': ''}, APPROVE)
    sources = docs('osc-mitchell-lama', 'hpd-response', 'osc-dashboard', 'hpd-foil')
    result = analyse(p, brief='NYC housing', geography='New York City', docs=sources)
    assert len(result.approved) == 1 and len(transport.requests) == 2
    assert transport.requests[0]['text']['format']['name'] == 'dochunt_evidence_analysis'
    leads, evidence = to_issue_records(result, sources, 'New York City')
    config = load_config(ROOT / 'newsroom.toml')
    issue = Issue('k', date(2026, 10, 6), evidence, leads, ['ok'], leads[0].provisional_headline, 'L1')
    html = render_issue(issue, config).html
    assert 'Still to prove' in html and 'Open housing code violations' in html
    assert 'HPD disagrees' in html  # agency disagreement is kept as the caveat


def test_headline_entity_must_appear_in_shown_facts():
    checked = check_lead(lead('Are hazards at Co-op City still open?'), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_presentation'


def test_fabricated_quote_makes_lead_unsupported():
    bad = dict(AUDIT_PERIOD, quote='The audit covered every Mitchell-Lama development in the city.')
    checked = check_lead(dict(lead(), established=[bad]), docs('osc-mitchell-lama', 'hpd-foil'))
    assert checked.status == 'unsupported'


def test_unsupported_qualification_rejects_rather_than_dropping_it():
    bad = dict(DISAGREEMENT, quote='HPD fully corrected every hazard identified by auditors.')
    checked = check_lead(dict(lead(), qualifications=[bad]), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'unsupported'


def test_route_location_must_be_attested_not_guessed():
    guessed = dict(VIOLATIONS, location='https://data.cityofnewyork.us/guessed-dataset')
    checked = check_lead(lead(records=(guessed,)), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_access'


@pytest.mark.parametrize('headline,problem', [
    ('How, if at all, did local child labor investigation targeting change after WHD removed specified data in 2026?', 'words'),
    ('Did WHD targeting change after HHS data was dropped?', 'acronyms'),
])
def test_presentation_gate(headline, problem):
    assert any(problem in x for x in presentation_problems(headline))
    checked = check_lead(lead(headline), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_presentation'


def test_request_route_needs_request_language_and_an_attested_destination():
    labor_docs = docs('gao-26-107722', 'whd-foia', 'whd-foia-request')
    fact = {'text': 'Requests by email go only to one address.', 'source': 'whd-foia-request',
            'quote': 'FOIA requests submitted by email will only be accepted if they are sent directly to foiarequest@dol.gov'}
    record = {'description': 'District child labor targeting plans, 2023-2026', 'could_reveal': 'how targets changed',
              'essential': True, 'kind': 'underlying', 'route': 'records_request', 'custodian': 'Wage and Hour Division',
              'location': 'foiarequest@dol.gov', 'how': 'Email the request.', 'source': 'whd-foia-request', 'quote': fact['quote']}
    base = {'headline': 'Did child labor inspections shift after federal targeting data was dropped?',
            'established': [fact], 'qualifications': [], 'hypothesis': 'h', 'already_answered': '',
            'records': [record], 'newsworthiness': 6}
    assert check_lead(dict(base, request_text=''), labor_docs).status == 'held_access'
    assert check_lead(dict(base, request_text='x' * 2000), labor_docs).status == 'held_access'
    request = 'Under the Freedom of Information Act, I request copies of existing records: district targeting plans for child labor investigations, 2023 to 2026.'
    assert check_lead(dict(base, request_text=request), labor_docs).status == 'ready'


def test_review_can_only_reject_and_nothing_is_rewritten():
    reject = {'verdicts': [{'lead': 'L1', 'verdict': 'reject', 'problems': ['access does not reach the records']}]}
    p, transport = provider({'leads': [lead()], 'no_lead_reason': ''}, reject)
    result = analyse(p, brief='b', geography='g', docs=docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert result.approved == [] and result.held[0]['status'] == 'rejected_by_review'
    assert len(transport.requests) == 2


def test_approve_with_problems_is_not_an_approval():
    mixed = {'verdicts': [{'lead': 'L1', 'verdict': 'approve', 'problems': ['headline overstates']}]}
    p, _ = provider({'leads': [lead()], 'no_lead_reason': ''}, mixed)
    result = analyse(p, brief='b', geography='g', docs=docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert result.approved == []


def test_review_must_judge_every_lead():
    p, _ = provider({'leads': [lead()], 'no_lead_reason': ''}, {'verdicts': []})
    with pytest.raises(EngineError):
        analyse(p, brief='b', geography='g', docs=docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))


def test_no_lead_is_a_completed_result():
    p, transport = provider({'leads': [], 'no_lead_reason': 'The question is already answered.'})
    result = analyse(p, brief='b', geography='g', docs=docs('seattle-rec-followup'))
    assert result.approved == [] and result.no_lead_reason and len(transport.requests) == 1


def test_whole_documents_reach_the_model_including_late_agency_comments():
    labor = docs('gao-26-107722')
    packed, partial = pack_sources(labor, 240000)
    assert partial == [] and 'Agency Comments' in packed[0]['text']
    # When a document cannot fit, its start and end survive with an explicit marker.
    packed, partial = pack_sources(labor, 40000)
    assert partial == ['gao-26-107722'] and 'omitted by DocHunt' in packed[0]['text']
    assert packed[0]['text'].endswith(labor[0].text[-1000:])


def test_acquisition_truncation_is_disclosed_to_the_model():
    (doc,) = docs('ca-auditor-2025-128')
    assert 'later pages were not read' in doc.note


def test_render_rejects_tampered_engine_route():
    p, _ = provider({'leads': [lead()], 'no_lead_reason': ''}, APPROVE)
    sources = docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil')
    result = analyse(p, brief='b', geography='New York City', docs=sources)
    leads, evidence = to_issue_records(result, sources, 'New York City')
    records = [dict(r, location='https://example.org/not-in-evidence') for r in leads[0].records_plan['records']]
    tampered = replace(leads[0], records_plan=dict(leads[0].records_plan, records=records))
    issue = Issue('k', date(2026, 10, 6), evidence, [tampered], ['ok'], tampered.provisional_headline, 'L1')
    with pytest.raises(ValueError):
        render_issue(issue, load_config(ROOT / 'newsroom.toml'))


def test_hunt_config_defaults_to_current_pipeline_and_validates():
    config = load_config(ROOT / 'newsroom.toml')
    assert config.hunt.mode == 'watch'
    data = {'identity': {'id': 'n', 'newsletter_name': 'N', 'sender_name': 'N'},
            'editorial': {'coverage_brief': 'b'}, 'ai': {'provider': 'openai', 'model': 'gpt-6.1-sol'},
            'email': {'provider': 'resend', 'sender': 'a@example.org', 'recipients': ['b@example.org']},
            'schedule': {'timezone': 'UTC'},
            'hunt': {'mode': 'search', 'playbook': 'playbooks/labor.toml', 'allowed_acronyms': ['OSHA']}}
    assert config_from_data(data).hunt.allowed_acronyms == ['OSHA']
    with pytest.raises(ValueError):
        config_from_data(dict(data, hunt={'mode': 'other'}))


@pytest.mark.parametrize('name', ['labor', 'politics', 'military'])
def test_starter_playbooks_load_and_contain_no_stories(name):
    playbook = load_playbook(ROOT / 'playbooks' / f'{name}.toml')
    assert playbook['record_families'] and playbook['reject_when']
    text = (ROOT / 'playbooks' / f'{name}.toml').read_text()
    assert 'verified = true' not in text  # starter URLs are never pre-verified access


def test_hunt_preview_writes_files_without_delivery(tmp_path):
    from dochunt.engine import hunt_preview
    config = load_config(ROOT / 'newsroom.toml')
    config.editorial.geography = 'New York City'
    p, _ = provider({'leads': [lead()], 'no_lead_reason': ''}, APPROVE)
    sources = docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil')
    issue, result = hunt_preview(config, p, tmp_path, docs=sources, local_date=date(2026, 10, 6))
    assert issue.subject == lead()['headline']
    saved = json.loads((tmp_path / 'engine-result.json').read_text())
    assert saved['calls'][0]['stage'] == 'analysis' and saved['approved']
    assert (tmp_path / 'preview.html').exists()


def test_hunt_preview_no_lead_still_renders_held_reasons(tmp_path):
    from dochunt.engine import hunt_preview
    config = load_config(ROOT / 'newsroom.toml')
    p, _ = provider({'leads': [lead(records=(FULL_AUDIT, CERTIFICATIONS))], 'no_lead_reason': ''})
    issue, _ = hunt_preview(config, p, tmp_path, docs=docs('osc-mitchell-lama', 'hpd-response'), local_date=date(2026, 10, 6))
    assert issue.leads == [] and any(n.startswith('held_access') for n in issue.coverage_results)


class SavedRetriever:
    """Serves saved receipts by URL; refuses anything else. No network."""
    def __init__(self, receipts):
        self.receipts, self.fetched = receipts, []

    def fetch(self, url, *, reserve_fetches=0):
        from dochunt.retrieval import Retrieved
        self.fetched.append(url)
        r = self.receipts.get(url)
        if r is None:
            return Retrieved(url, None, 'Public source unavailable')
        return Retrieved(url, r['text'], 'Public document retrieved', '2026-10-05T00:00:00Z', hashlib.sha256(r['text'].encode()).hexdigest(),
                         'html', r['title'], [{'locator': 'text', 'text': r['text']}], r['links'])


def test_discovery_reads_found_documents_and_records_unread_ones():
    from dochunt.engine import discover
    saved = {json.loads((ROOT / 'eval/sources' / f'{i}.json').read_text())['url']: json.loads((ROOT / 'eval/sources' / f'{i}.json').read_text())
             for i in ('osc-fonda', 'fonda-minutes-2026-01')}
    urls = list(saved) + ['https://example.org/unreachable.pdf']
    search = {'status': 'completed', 'usage': {'input_tokens': 50, 'output_tokens': 20}, 'output': [
        {'type': 'web_search_call', 'id': 'ws1', 'status': 'completed', 'action': {'type': 'search', 'sources': [{'url': u} for u in urls]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Found minutes and an audit.'}]}]}
    from dochunt.watch import retriever_fetch
    p = OpenAIProvider('gpt-6.1-sol', Budget(Limits()), FixtureTransport([search]))
    p.retriever = SavedRetriever(saved)
    found, meta = discover(p, retriever_fetch(p.retriever), brief='local government', geography='Fonda, New York')
    assert {d.url for d in found} == set(saved)
    assert meta['not_read'] and meta['not_read'][0]['url'] == 'https://example.org/unreachable.pdf'
    assert p.retriever.fetched  # documents were fetched, not taken from search snippets


def test_search_mode_runs_through_hunt_preview(tmp_path):
    from dochunt.engine import hunt_preview
    saved = {json.loads((ROOT / 'eval/sources' / f'{i}.json').read_text())['url']: json.loads((ROOT / 'eval/sources' / f'{i}.json').read_text())
             for i in ('osc-fonda', 'fonda-minutes-2026-01')}
    search = {'status': 'completed', 'usage': {'input_tokens': 50, 'output_tokens': 20}, 'output': [
        {'type': 'web_search_call', 'id': 'ws1', 'status': 'completed', 'action': {'type': 'search', 'sources': [{'url': u} for u in saved]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': 'Found minutes and an audit.'}]}]}
    config = load_config(ROOT / 'examples/education.toml')
    config.hunt.mode, config.sources = 'search', []
    p = OpenAIProvider('gpt-6.1-sol', Budget(Limits()),
                       FixtureTransport([search, wire({'leads': [], 'no_lead_reason': 'Nothing worth requesting.'})]))
    p.retriever = SavedRetriever(saved)
    issue, _ = hunt_preview(config, p, tmp_path, local_date=date(2026, 10, 5))
    assert issue.leads == [] and set(p.retriever.fetched) >= set(saved) and len(p.transport.requests) == 2


def test_reviewer_model_makes_the_review_call(tmp_path, monkeypatch):
    from dochunt.cli import engine_preview
    from support import config as fictional_config, fictional, wire as fwire
    c = fictional_config()
    c.ai.reviewer_model = 'gpt-6-astra'
    analysis, review = fictional()['outputs']
    p = OpenAIProvider('gpt-6.1-sol', Budget(c.limits), FixtureTransport([fwire(analysis)]), api_key='synthetic-unused')
    from support import Retriever as FictionalRetriever
    p.retriever = FictionalRetriever()
    reviewer = OpenAIProvider('gpt-6-astra', p.budget, FixtureTransport([fwire(review)]), api_key='synthetic-unused')
    made = []
    def fake_create(provider, model, budget, api_key=None):
        made.append((model, budget is p.budget))
        return reviewer
    monkeypatch.setattr('dochunt.cli.create_provider', fake_create)
    monkeypatch.setattr(OpenAIProvider, 'preflight', lambda self: None)
    engine_preview(c, tmp_path, provider=p, local_date=date(2026, 10, 2))
    assert made == [('gpt-6-astra', True)]
    assert len(p.transport.requests) == 1 and len(reviewer.transport.requests) == 1
    assert reviewer.transport.requests[0]['model'] == 'gpt-6-astra'


def test_eval_runner_offline_replays_recorded_responses(tmp_path, monkeypatch):
    import runpy, sys
    recorded = tmp_path / 'responses' / 'R3b-housing-mitchell-lama-with-hpd-foil-page'
    recorded.mkdir(parents=True)
    (recorded / 'analysis.json').write_text(json.dumps(wire({'leads': [lead()], 'no_lead_reason': ''})))
    (recorded / 'review.json').write_text(json.dumps(wire(APPROVE)))
    monkeypatch.setattr(sys, 'argv', ['run_eval', '--case', 'R3b', '--responses', str(tmp_path / 'responses'), '--output', str(tmp_path / 'out')])
    runpy.run_path(str(ROOT / 'tools/run_eval.py'), run_name='__main__')
    (row,) = json.loads((tmp_path / 'out/summary.json').read_text())
    assert row['leads'] == 1 and row['input_tokens'] > 0 and 'run_failure' not in row


def test_invented_address_in_prose_is_caught():
    sneaky = dict(VIOLATIONS, how='Email records@hpd-example.gov or see https://data.example.gov/hpd')
    checked = check_lead(lead(records=(sneaky,)), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_access'
    assert sum('address not found' in p for p in checked.problems) == 2


def test_stage_output_caps_are_applied_and_restored():
    p, transport = provider({'leads': [lead()], 'no_lead_reason': ''}, APPROVE)
    analyse(p, brief='b', geography='g', docs=docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert [r['max_output_tokens'] for r in transport.requests] == [12000, 4000]
    assert p.stage_output_cap == 6000


# Regressions from the independent review of 2ee047e.

def test_mislabelled_route_to_the_audit_cannot_stand_in_for_underlying_records():
    # A model marks the certification records as a public download located at the
    # audit PDF (a real link on the landing page) with an unrelated quote.
    (landing,) = docs('osc-mitchell-lama')
    pdf = next(l for l in landing.links if l.endswith('sga-2026-24n4.pdf'))
    mislabelled = dict(CERTIFICATIONS, route='public_download', location=pdf,
                       source='osc-mitchell-lama', quote=AUDIT_PERIOD['quote'], how='Download the audit.')
    checked = check_lead(lead(records=(mislabelled,)), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_access'
    assert any('does not describe this record' in p for p in checked.problems)


def test_route_to_an_already_supplied_document_is_not_further_records():
    (landing,) = docs('osc-mitchell-lama')
    pointed = dict(VIOLATIONS, location=landing.url)
    checked = check_lead(lead(records=(pointed,)), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_access'


def test_request_route_must_name_its_custodian_on_the_cited_page():
    fact = {'text': 'Requests by email go only to one address.', 'source': 'whd-foia-request',
            'quote': 'FOIA requests submitted by email will only be accepted if they are sent directly to foiarequest@dol.gov'}
    record = {'description': 'District targeting plans', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Bureau of Labor Statistics', 'location': 'foiarequest@dol.gov',
              'how': 'Email the request.', 'source': 'whd-foia-request', 'quote': fact['quote']}
    request = 'Under the Freedom of Information Act, I request copies of existing records: district targeting plans for child labor investigations, 2023 to 2026.'
    base = {'headline': 'Did child labor inspections shift after federal targeting data was dropped?', 'established': [fact],
            'qualifications': [], 'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 6,
            'request_text': request}
    assert check_lead(base, docs('whd-foia-request')).status == 'held_access'


def test_location_with_one_invented_address_fails_and_all_rendered_fields_are_scanned():
    mixed = dict(VIOLATIONS, location="HPD's Open Data portal https://invented.example/data")
    assert check_lead(lead(records=(mixed,)), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil')).status == 'held_access'
    hidden = lead(hypothesis='See www.invented-portal.example for the data.')
    checked = check_lead(hidden, docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'held_access' and any('www.invented-portal.example' in p for p in checked.problems)


def test_fact_with_a_number_absent_from_its_source_is_unsupported():
    wrong = dict(AUDIT_PERIOD, text='The audit covered January 2019 through April 2025 and 4,000 apartments.')
    checked = check_lead(dict(lead(), established=[PROGRAM, wrong]), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'))
    assert checked.status == 'unsupported'


def test_request_with_unresolved_identifying_placeholder_is_held():
    fact = {'text': 'Requests by email go only to one address.', 'source': 'whd-foia-request',
            'quote': 'FOIA requests submitted by email will only be accepted if they are sent directly to foiarequest@dol.gov'}
    record = {'description': 'District targeting plans', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Wage and Hour Division', 'location': 'foiarequest@dol.gov',
              'how': 'Email the request.', 'source': 'whd-foia-request', 'quote': fact['quote']}
    request = 'I request existing records, including targeting plans for [insert districts], for 2023 through 2026, under the FOIA.'
    lead_ = {'headline': 'Did child labor inspections shift after federal targeting data was dropped?', 'established': [fact],
             'qualifications': [], 'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 6,
             'request_text': request}
    assert check_lead(lead_, docs('whd-foia-request')).status == 'held_access'


def test_linked_route_must_come_from_its_cited_page_at_render(tmp_path):
    (foil,) = docs('hpd-foil')
    portal = foil.links[0]
    linked = dict(VIOLATIONS, location=portal)
    p, _ = provider({'leads': [lead(records=(linked,))], 'no_lead_reason': ''}, APPROVE)
    sources = docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil')
    result = analyse(p, brief='b', geography='New York City', docs=sources)
    assert result.approved, result.held
    leads, evidence = to_issue_records(result, sources, 'New York City')
    config = load_config(ROOT / 'newsroom.toml')
    issue = Issue('k', date(2026, 10, 6), evidence, leads, ['ok'], leads[0].provisional_headline, 'L1')
    render_issue(issue, config)
    for change in ({'location': 'https://evil.example/form'},  # location only
                   {'location': 'https://evil.example/form', 'linked_from': 'https://evil.example/'}):
        records = [dict(r, **change) for r in leads[0].records_plan['records']]
        tampered = replace(leads[0], records_plan=dict(leads[0].records_plan, records=records))
        with pytest.raises(ValueError):
            render_issue(Issue('k', date(2026, 10, 6), evidence, [tampered], ['ok'], tampered.provisional_headline, 'L1'), config)


def test_topic_words_alone_do_not_make_a_route_reach_a_record():
    # Review probe: topic words shared with any page cleared the overlap test.
    (landing,) = docs('osc-mitchell-lama')
    pdf = next(l for l in landing.links if l.endswith('sga-2026-24n4.pdf'))
    record = dict(CERTIFICATIONS, description='Annual inspection certifications submitted by Mitchell-Lama developments to HPD',
                  route='public_download', location=pdf, source='osc-mitchell-lama',
                  quote='There are both City-supervised and State-supervised Mitchell-Lama developments.')
    assert check_lead(lead(records=(record,)), docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil')).status == 'held_access'


def test_non_integer_newsworthiness_is_an_engine_error():
    p, _ = provider({'leads': [dict(lead(), newsworthiness='high')], 'no_lead_reason': ''})
    with pytest.raises(EngineError):
        analyse(p, brief='b', geography='g', docs=docs('osc-mitchell-lama', 'hpd-foil'))


def test_eval_runner_stops_batch_after_a_run_failure(tmp_path, monkeypatch):
    import runpy, sys
    for case in ('R3-housing-mitchell-lama-no-route', 'R3b-housing-mitchell-lama-with-hpd-foil-page'):
        (tmp_path / 'responses' / case).mkdir(parents=True)
        (tmp_path / 'responses' / case / 'analysis.json').write_text(json.dumps(wire({'not': 'valid'})))
    monkeypatch.setattr(sys, 'argv', ['run_eval', '--case', 'R3', '--responses', str(tmp_path / 'responses'), '--output', str(tmp_path / 'out')])
    runpy.run_path(str(ROOT / 'tools/run_eval.py'), run_name='__main__')
    rows = json.loads((tmp_path / 'out/summary.json').read_text())
    assert len(rows) == 1 and 'run_failure' in rows[0]


def test_largest_supplied_case_fits_the_eval_allowance_whole():
    # Budget.check adds the next payload's serialized bytes to usage so far. Treat the
    # analysis call as costing its full byte size (conservative) and require that the
    # review call, which repeats the sources plus leads, still fits the runner's allowance.
    import tomllib
    from dochunt.engine import _prompt
    cases = tomllib.loads((ROOT / 'eval/cases.toml').read_text())['case']
    p = OpenAIProvider('gpt-6.1-sol', Budget(Limits()))
    largest = 0
    for case in (c for c in cases if c['track'] == 'supplied'):
        packed, partial = pack_sources(docs(*case['sources']), 130000)
        assert partial == [], case['id']
        prompt = json.dumps({'brief': case['brief'], 'sources': packed}, ensure_ascii=False)
        payload = p.structured_payload(p.payload(prompt, 12000, 0, 0, _prompt('evidence-analysis')), 'evidence_analysis')
        largest = max(largest, len(json.dumps(payload).encode()))
    assert 2 * largest + 30000 <= 350000  # review adds up to three leads of JSON


# Regressions from the first live Phase A attempt (2026-10-06): the gate accepted a
# linked route whose location was a name plus an attested URL, the render check
# required a bare URL, and the paid output was lost because files were written last.

def test_name_plus_linked_url_location_passes_gate_and_render(tmp_path):
    from dochunt.engine import hunt_preview
    (foil,) = docs('hpd-foil')
    url = next(l for l in foil.links if 'openrecords' in l)
    mixed = dict(VIOLATIONS, location=f'NYC OpenRecords portal {url}')
    config = load_config(ROOT / 'newsroom.toml')
    p, _ = provider({'leads': [lead(records=(mixed,))], 'no_lead_reason': ''}, APPROVE)
    issue, result = hunt_preview(config, p, tmp_path, docs=docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'),
                                 local_date=date(2026, 10, 6))
    assert len(issue.leads) == 1, result.held


def test_paid_outputs_are_saved_before_render_and_render_failures_are_held(tmp_path, monkeypatch):
    import dochunt.engine as engine
    from dochunt.engine import hunt_preview

    def reject(*args, **kwargs):
        raise ValueError('simulated render disagreement')
    monkeypatch.setattr(engine, 'validate_engine_lead', reject)
    config = load_config(ROOT / 'newsroom.toml')
    p, _ = provider({'leads': [lead()], 'no_lead_reason': ''}, APPROVE)
    issue, result = hunt_preview(config, p, tmp_path, docs=docs('osc-mitchell-lama', 'hpd-response', 'hpd-foil'),
                                 local_date=date(2026, 10, 6))
    assert issue.leads == [] and result.held[-1]['status'] == 'render_rejected'
    for stage in ('analysis', 'review'):
        saved = json.loads((tmp_path / f'stage-{stage}.json').read_text())
        assert saved['output_text'] and saved['meta']['usage']['input_tokens'] > 0
    assert json.loads((tmp_path / 'engine-result.json').read_text())['emailed'] == []


def test_analysis_output_is_saved_even_when_it_is_malformed(tmp_path):
    from dochunt.engine import hunt_preview
    config = load_config(ROOT / 'newsroom.toml')
    p, _ = provider({'not': 'valid'})
    with pytest.raises(EngineError):
        hunt_preview(config, p, tmp_path, docs=docs('osc-mitchell-lama'), local_date=date(2026, 10, 6))
    assert json.loads((tmp_path / 'stage-analysis.json').read_text())['output_text']


def test_runner_charges_actual_usage_for_local_failures(tmp_path, monkeypatch):
    import runpy, sys
    case = 'R3-housing-mitchell-lama-no-route'
    (tmp_path / 'responses' / case).mkdir(parents=True)
    (tmp_path / 'responses' / case / 'analysis.json').write_text(json.dumps(wire({'not': 'valid'})))
    monkeypatch.setattr(sys, 'argv', ['run_eval', '--case', 'R3-', '--responses', str(tmp_path / 'responses'), '--output', str(tmp_path / 'out')])
    runpy.run_path(str(ROOT / 'tools/run_eval.py'), run_name='__main__')
    (row,) = json.loads((tmp_path / 'out/summary.json').read_text())
    assert row['run_failure'].startswith('EngineError') and not row.get('charged_worst_case')
    assert row['list_price_usd'] < 0.01


def test_runner_records_raw_wire_even_when_provider_rejects_it(tmp_path, monkeypatch):
    # A response cut off at the output cap raises inside the provider layer before the
    # engine's sink; the runner's transport must already have saved it.
    import runpy, sys
    case = 'R3-housing-mitchell-lama-no-route'
    truncated = dict(wire({'leads': []}), status='incomplete')
    (tmp_path / 'responses' / case).mkdir(parents=True)
    (tmp_path / 'responses' / case / 'analysis.json').write_text(json.dumps(truncated))
    monkeypatch.setattr(sys, 'argv', ['run_eval', '--case', 'R3-', '--responses', str(tmp_path / 'responses'), '--output', str(tmp_path / 'out')])
    runpy.run_path(str(ROOT / 'tools/run_eval.py'), run_name='__main__')
    (row,) = json.loads((tmp_path / 'out/summary.json').read_text())
    assert row['run_failure'].startswith('ProviderError') and row['charged_worst_case']
    assert json.loads((tmp_path / 'out' / case / 'wire-1.json').read_text())['status'] == 'incomplete'
