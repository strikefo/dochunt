"""Regressions from the live Phase A run (2026-10-06).

tests/fixtures/live-phase-a holds the real gpt-6.1-sol analysis outputs for the 15 frozen
supplied-source cases. Under the gates as run, all 27 leads were held, most by gate
defects. These tests pin the corrected behavior. The gate fixes were made after reading
these outputs, so passing them is diagnostic, not evidence that the engine generalizes.
"""
import json
import tomllib
from pathlib import Path

import pytest

from dochunt.engine import (SourceDoc, _numbers, _validate_analysis, check_lead, custodian_named,
                            literal_span, request_problems, source_from_receipt)

ROOT = Path(__file__).resolve().parents[1]
CASES = {c['id']: c for c in tomllib.loads((ROOT / 'eval/cases.toml').read_text())['case']}

# Corrected-gate statuses per case, in lead order. Holds that remain are intended:
# no route in the sources (R3, U7), no explicit submission wording for a linked route (R4), blank model locations (U4, U10), model slips
# (U5 dropped word, U6 derived total, U10 typo) and one headline name absent from the
# lead's evidence (U1).
EXPECTED = {
    'R1-labor-child-labor-targeting': ['ready', 'ready', 'ready'],
    'R2-education-absenteeism-data': ['ready', 'ready'],
    'R3-housing-mitchell-lama-no-route': ['held_access', 'held_access'],
    'R3b-housing-mitchell-lama-with-hpd-foil-page': ['ready', 'ready'],
    # R4: the clerk link's page mentions FOIL only as "point of contact"; held conservatively.
    'R4-local-fonda-water-fund': ['held_access', 'held_access'],
    'U1-local-health-landfill-response': ['ready', 'held_presentation'],
    'U10-local-suffolk-contract-disclosure': ['unsupported', 'held_access'],
    # U2, U8: custodian given only as a generic title ("Office of Inspector General (OIG)").
    'U2-local-chicago-auto-pounds': ['held_access', 'held_access'],
    'U3-local-seattle-recommendation-status': ['ready', 'ready', 'ready'],
    'U4-local-lansing-procurement': ['held_access'],
    'U5-labor-osha-oig-alert': ['held_access'],
    'U6-labor-ui-improper-payments': ['unsupported'],
    'U7-education-oregon-high-school-success': ['held_access', 'held_access'],
    'U8-education-puerto-rico-esser': ['held_access'],
    'U9-education-special-education-placement': ['ready'],
}


def case_docs(case_id):
    return [source_from_receipt(json.loads((ROOT / 'eval/sources' / f'{s}.json').read_text()), s)
            for s in CASES[case_id]['sources']]


@pytest.mark.parametrize('case_id', sorted(EXPECTED))
def test_saved_live_output_gate_statuses(case_id):
    leads = _validate_analysis(json.loads((ROOT / 'tests/fixtures/live-phase-a' / f'{case_id}.json').read_text()))
    docs = case_docs(case_id)
    statuses = [check_lead(lead, docs, geography=CASES[case_id]['geography']).status for lead in leads]
    assert statuses == EXPECTED[case_id]


def test_no_route_in_sources_is_never_ready():
    # The 2026-10-06 false-positive case: no HPD route was supplied.
    leads = _validate_analysis(json.loads((ROOT / 'tests/fixtures/live-phase-a/R3-housing-mitchell-lama-no-route.json').read_text()))
    assert all(check_lead(l, case_docs('R3-housing-mitchell-lama-no-route')).status != 'ready' for l in leads)


def test_quote_marks_and_pdf_page_headers_do_not_break_literal_matching():
    header = 'Status Report on Implementation of City Auditor Recommendations as of December 2025'
    doc = SourceDoc('d', 'https://example.gov/r.pdf', 't',
                    f'{header} Page 3 Background. In 2025, SPR filled vacant positions and Page 4 {header} '
                    'hired more seasonal staff to assist.')
    span = literal_span('In 2025, SPR filled vacant positions and hired more seasonal staff to assist.', doc)
    assert span and 'Page 4' in span  # evidence keeps the source's own text
    assert literal_span('“In 2025, SPR filled vacant positions and', doc)
    assert literal_span('In 2025, SPR filled vacant positions and fired more seasonal staff to assist.', doc) is None


def test_numbers_compare_by_value():
    assert 43159.0 in _numbers('$43,159.00') and 2.0 in _numbers('October 02, 2026')


def test_custodian_check_rejects_a_different_office_and_accepts_any_department_portals():
    assert not custodian_named('Bureau of Labor Statistics', 'A FOIA request can be made for any agency record.')
    assert custodian_named('Seattle Police Department', 'submit public disclosure requests (PDRs) to any city department')


def test_questions_only_contact_without_any_submission_passage_is_held():
    page = SourceDoc('p', 'https://example.gov/records', 'Records',
                     'The Records Office of Example County holds permit files. '
                     'If you have questions, contact the Records Office at help@example.gov.')
    fact = {'text': 'The county holds permit files.', 'source': 'p', 'quote': 'The Records Office of Example County holds permit files.'}
    record = {'description': 'Permit files', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Example County Records Office', 'location': 'help@example.gov',
              'how': 'Email.', 'source': 'p', 'quote': 'If you have questions, contact the Records Office at help@example.gov.'}
    lead = {'headline': 'Are Example County permits complete?', 'established': [fact], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 5,
            'request_text': 'I request copies of existing records: permit files issued in 2025, under the public records law.'}
    checked = check_lead(lead, [page])
    assert checked.status == 'held_access' and any('submit requests' in p for p in checked.problems)


def test_request_language_accepts_plain_existing_records_wording():
    assert request_problems('To the Records Access Officer:\nPlease provide the following existing records held by the agency, 2023-2026: 1. Vendor ledgers.') == []
    assert request_problems('Please explain why the agency stopped its reviews and what it plans to do next.')


# Negative cases from the independent review of 7aee011.

def test_page_gap_may_not_hide_content_that_is_not_a_running_header():
    doc = SourceDoc('d', 'https://example.gov/r.pdf', 't',
                    'The facility met every safety requirement Page 7 except those noted in Appendix B, which were never '
                    'corrected and remain open. Most were resolved.')
    assert literal_span('The facility met every safety requirement Most were resolved.', doc) is None


def test_address_of_a_different_office_on_another_supplied_page_is_held():
    ed, dol = (source_from_receipt(json.loads((ROOT / 'eval/sources' / f'{s}.json').read_text()), s)
               for s in ('ed-oig-foia', 'dol-oig-foia'))
    dol_email = next(t for t in __import__('re').findall(r'[\w.+-]+@[\w-]+\.[\w.]+', dol.text) if 'oig' in t.lower())
    fact = {'text': 'The Education inspector general handles requests for its records.', 'source': 'ed-oig-foia',
            'quote': literal_span(ed.text[:200].rsplit(' ', 3)[0], ed) or ed.text[:60]}
    record = {'description': 'Inspection files', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'U.S. Department of Education Office of Inspector General',
              'location': dol_email, 'how': 'Email.', 'source': 'ed-oig-foia', 'quote': fact['quote']}
    lead = {'headline': 'Did the agency recover the money?', 'established': [fact], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 5,
            'request_text': 'I request copies of existing records: inspection files for 2025, under the FOIA.'}
    checked = check_lead(lead, [ed, dol])
    assert checked.status == 'held_access' and any('custodian is not named' in p for p in checked.problems), checked.problems


def test_generic_custodian_title_does_not_match_any_agency():
    assert not custodian_named('Office of Inspector General', 'The Department of Labor Office of Inspector General accepts FOIA requests.')


def test_name_only_public_location_needs_its_own_describing_sentence():
    # The model's quote mentions a downloadable dataset, but the "location" it names is
    # only found in an unrelated sentence; that sentence must describe the record itself.
    page = SourceDoc('p', 'https://example.gov/a', 'Audit', 'The permit files dataset is available for download. '
                     'Questions may be directed to the Office of Inspector General.')
    fact = {'text': 'A permit files dataset is available.', 'source': 'p', 'quote': 'The permit files dataset is available for download.'}
    record = {'description': 'Permit files dataset', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'public_download', 'custodian': '', 'location': 'Office of Inspector General', 'how': 'Download.',
              'source': 'p', 'quote': 'The permit files dataset is available for download.'}
    lead = {'headline': 'Are permit files complete?', 'established': [fact], 'qualifications': [], 'hypothesis': 'h',
            'already_answered': '', 'records': [record], 'newsworthiness': 5, 'request_text': ''}
    assert check_lead(lead, [page]).status == 'held_access'


def test_hotline_or_complaint_channel_is_not_a_records_submission_route():
    from dochunt.engine import is_submission_text
    assert not is_submission_text('To report fraud, file a complaint with the Hotline at hotline@x.gov. We do not process FOIA requests here.')
    assert is_submission_text('Complaint records and permit files must be submitted through the CPRA Records Request Form via email to a@x.gov.')


def test_headline_names_match_whole_words_only():
    from dochunt.engine import headline_grounding
    assert headline_grounding('Did Lee review Rand contracts?', ['The fleet grand total rose.'])


# Negative cases from the independent review of 61ccae8.

def test_office_name_is_not_a_public_download_location():
    (ed,) = (source_from_receipt(json.loads((ROOT / 'eval/sources/ed-oig-foia.json').read_text()), 'ed-oig-foia'),)
    fact_quote = literal_span(ed.text[:200].rsplit(' ', 3)[0], ed)
    fact = {'text': 'The inspector general publishes FOIA guidance.', 'source': 'ed-oig-foia', 'quote': fact_quote}
    record = {'description': 'Inspector General audit workpapers', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'public_download', 'custodian': '', 'location': 'Office of Inspector General', 'how': 'Download.',
              'source': 'ed-oig-foia', 'quote': fact_quote}
    lead = {'headline': 'Did the agency recover the money?', 'established': [fact], 'qualifications': [], 'hypothesis': 'h',
            'already_answered': '', 'records': [record], 'newsworthiness': 5, 'request_text': ''}
    assert check_lead(lead, [ed]).status == 'held_access'


def test_another_office_on_the_same_page_cannot_vouch_for_the_custodian():
    # Real LA County Public Health records page: Hazmat requests go to the Fire Department.
    page = source_from_receipt(json.loads((ROOT / 'eval/sources/la-dph-prr-policy.json').read_text()), 'la-dph-prr-policy')
    fire = 'https://fire.lacounty.gov/public-records-requests/'
    fact_quote = literal_span(page.text[page.text.find('The Custodian of Records coordinates'):][:120].rsplit(' ', 2)[0], page)
    fact = {'text': 'The custodian coordinates requests.', 'source': 'la-dph-prr-policy', 'quote': fact_quote}
    record = {'description': 'Hazardous materials inspection files', 'could_reveal': 'x', 'essential': True, 'kind': 'underlying',
              'route': 'records_request', 'custodian': 'Los Angeles County Department of Public Health', 'location': fire,
              'how': 'Use the portal.', 'source': 'la-dph-prr-policy', 'quote': fact_quote}
    lead = {'headline': 'Are hazardous materials inspections complete?', 'established': [fact], 'qualifications': [],
            'hypothesis': 'h', 'already_answered': '', 'records': [record], 'newsworthiness': 5,
            'request_text': 'I request copies of existing records: hazardous materials inspection files for 2025, under the CPRA.'}
    checked = check_lead(lead, [page])
    assert checked.status == 'held_access', checked.problems
