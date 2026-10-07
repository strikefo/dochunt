"""Issue-level validation for engine leads (the only lead format)."""
from dataclasses import replace
from datetime import date

import pytest

from dochunt.editorial import lead_identity, lead_revision, validate_issue, validate_request_placeholders
from dochunt.models import Issue
from support import config, sample_issue


def test_sample_issue_validates():
    issue = sample_issue()
    validate_issue(issue, config())
    assert issue.leads[0].records_plan['engine'] == 'evidence-v1'


def test_lead_cap_enforced():
    c = config()
    c.editorial.max_final_leads = 1
    issue = sample_issue(c)
    two = Issue(issue.issue_key, issue.local_date, issue.evidence, issue.leads + [replace(issue.leads[0], id='L2')],
                issue.coverage_results, issue.subject, issue.subject_lead_id)
    with pytest.raises(ValueError):
        validate_issue(two, c)


def test_non_engine_leads_are_rejected():
    issue = sample_issue()
    old = replace(issue.leads[0], records_plan={})
    with pytest.raises(ValueError):
        validate_issue(Issue(issue.issue_key, issue.local_date, issue.evidence, [old], issue.coverage_results,
                             issue.subject, issue.subject_lead_id), config())


def test_source_must_match_referenced_evidence():
    issue = sample_issue()
    moved = replace(issue.leads[0], source_url='https://elsewhere.example/doc')
    with pytest.raises(ValueError):
        validate_issue(Issue(issue.issue_key, issue.local_date, issue.evidence, [moved], issue.coverage_results,
                             issue.subject, issue.subject_lead_id), config())


def test_identity_is_stable_and_revision_tracks_facts():
    lead = sample_issue().leads[0]
    assert lead_identity(lead) == lead_identity(replace(lead, provisional_headline='Different headline?'))
    assert lead_revision(lead) != lead_revision(replace(lead, factual_hook='A different established fact.'))


def test_request_placeholders_for_record_identity_are_rejected():
    with pytest.raises(ValueError):
        validate_request_placeholders('I request records for [insert contracts] from 2025.')
    validate_request_placeholders('I request records. [Insert your name]')
