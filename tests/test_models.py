import pytest
from dochunt.models import IssueState, Lead

def lead(score=5, verdict='approved'):
    return Lead(id='one',provisional_headline='School spending',story_potential='Public accountability',factual_hook='Audit published',evidence_ids=['e1'],source_url='https://example.org/audit',likely_records=['Contracts'],acquisition_route='public',acquisition_details='Download records',newsworthiness=score,obtainability=5,next_action='Read audit',reviewer_verdict=verdict)

def test_schema_rejects_unknown_status_and_invalid_score():
    assert {s.value for s in IssueState} == {'prepared','accepted','failed','unknown'}
    with pytest.raises(ValueError): IssueState('delivered')
    for score in [0,11,True,5.5]:
        with pytest.raises(ValueError): lead(score)
    with pytest.raises(ValueError): lead(verdict='unchecked')
    assert lead().newsworthiness == 5

def test_issue_rejects_missing_evidence_and_ungrounded_subject():
    from datetime import date
    from dochunt.models import Evidence, Issue
    e=Evidence('e1','https://example.org/audit','Audit','Audit published','2026-10-02T12:00:00Z')
    params=dict(issue_key='example:2026-10-02',local_date=date(2026,10,2),evidence=[e],leads=[lead()],coverage_results=['Complete'],subject='School spending',subject_lead_id='one')
    assert Issue(**params).leads[0].id == 'one'
    for changes in [dict(evidence=[]),dict(subject='Unsupported allegation'),dict(subject_lead_id='missing')]:
        with pytest.raises(ValueError): Issue(**(params|changes))


def test_receipt_distinguishes_acceptance_from_delivery():
    from dochunt.models import SendReceipt
    with pytest.raises(ValueError): SendReceipt('resend','delivered')
    with pytest.raises(ValueError): SendReceipt('resend','accepted')
    assert SendReceipt('smtp','unknown').state == IssueState.UNKNOWN


def test_issue_subject_cannot_append_unsupported_assertions():
    from datetime import date
    from dochunt.models import Evidence, Issue
    params=dict(issue_key='example:2026-10-02',local_date=date(2026,10,2),evidence=[Evidence('e1','https://example.org/audit','Audit','Audit published','2026-10-02T12:00:00Z')],leads=[lead()],coverage_results=['Complete'],subject_lead_id='one')
    for subject in ['School spending proves corruption', 'Document Hunt: School spending', 'School spending — millions stolen']:
        with pytest.raises(ValueError): Issue(**params,subject=subject)
    assert Issue(**params,subject='School spending').subject == 'School spending'


def test_no_leads_subject_is_fixed_and_neutral():
    from datetime import date
    from dochunt.models import Issue
    params=dict(issue_key='example:2026-10-02',local_date=date(2026,10,2),evidence=[],leads=[],coverage_results=['Research completed; no qualifying leads'])
    with pytest.raises(ValueError): Issue(**params,subject='Corruption across schools')
    assert Issue(**params,subject='No qualifying document leads').subject == 'No qualifying document leads'
