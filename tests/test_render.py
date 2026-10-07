"""Email rendering of engine issues: concise layout, escaping, branding, safe links."""
from dataclasses import replace
from datetime import date

import pytest

from dochunt.models import Issue
from dochunt.render import render_issue
from support import config, sample_issue


def test_layout_shows_new_document_facts_records_and_request():
    c = config()
    issue = sample_issue(c)
    r = render_issue(issue, c)
    for label in ['1. ' + issue.subject, 'New document', 'Established', 'Still to prove', 'Get the records',
                  'Send the request to: records@publicworks.example', 'This briefing is AI-generated']:
        assert label in r.html and label in r.text
    # The new document is the source, so no separate Source line repeats it.
    assert 'Source:' not in r.text
    assert r.subject == issue.subject and len(r.fingerprint) == 64 and render_issue(issue, c) == r


def test_internal_metadata_and_raw_quotes_are_not_shown():
    c = config()
    issue = sample_issue(c)
    r = render_issue(issue, c)
    for hidden in ['records_plan', 'evidence_ids', 'route_notes', 'newsworthiness', 'evidence-v1']:
        assert hidden not in r.html and hidden not in r.text
    # The full quoted audit sentence is evidence, not email copy.
    assert issue.evidence[0].excerpt not in r.text


def test_content_is_escaped():
    c = config()
    issue = sample_issue(c)
    evil = replace(issue.leads[0], story_potential='<script>evil</script>')
    r = render_issue(Issue(issue.issue_key, issue.local_date, issue.evidence, [evil], issue.coverage_results,
                           issue.subject, issue.subject_lead_id), c)
    assert '<script>' not in r.html and '&lt;script&gt;' in r.html


def test_unsafe_links_are_rejected():
    c = config()
    issue = sample_issue(c)
    issue.leads[0].source_url = 'javascript:alert(1)'
    with pytest.raises(ValueError):
        render_issue(issue, c)


def test_no_lead_email_is_neutral_and_hides_internal_notes():
    c = config()
    issue = Issue(f'{c.identity.id}:2026-10-02', date(2026, 10, 2), [], [], ['held_access: secret internal note'],
                  'No qualifying document leads')
    r = render_issue(issue, c)
    assert 'No qualifying document leads' in r.text and 'secret internal note' not in r.html
    assert 'AI-generated' not in r.text


def test_accent_branding_changes_deterministic_payload():
    c = config()
    issue = sample_issue(c)
    before = render_issue(issue, c)
    c.identity.accent_color = '#abcdef'
    after = render_issue(issue, c)
    assert 'background:#abcdef;color:#17202a' in after.html
    assert after.fingerprint != before.fingerprint and after == render_issue(issue, c)


def test_email_uses_inline_tables_and_wraps_long_text_on_mobile():
    c = config()
    r = render_issue(sample_issue(c), c)
    assert 'role="presentation"' in r.html and 'max-width:680px' in r.html
    assert 'overflow-wrap:anywhere' in r.html and '@media only screen and (max-width:600px)' in r.html
