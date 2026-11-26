from datetime import date
from support import sample_issue
from dochunt.tracker import record_issue, sent_keys


def test_records_each_sent_lead_once_per_source():
    rows = record_issue([], sample_issue(local_date=date(2026, 11, 20)))
    assert len(rows) == 3 and len(sent_keys(rows)) == 3
    assert all(r['first_sent'] == r['last_issue_included'] == '2026-11-20' and r['times_sent'] == 1 for r in rows)


def test_resending_an_older_issue_never_rolls_the_tracker_back():
    newer = record_issue([], sample_issue(local_date=date(2026, 11, 20)))
    newer[0]['headline'] = 'Updated headline'
    rows = record_issue(newer, sample_issue(local_date=date(2026, 7, 1)))
    assert rows[0]['last_issue_included'] == '2026-11-20' and rows[0]['headline'] == 'Updated headline'
    assert rows[0]['first_sent'] == '2026-07-01' and rows[0]['times_sent'] == 2
