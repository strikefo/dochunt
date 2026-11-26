"""Sent-lead tracker rows, stored in data/leads.jsonl alongside delivery state."""
import hashlib
from .models import source_key


def lead_id(url):
    return hashlib.sha256(source_key(url).encode()).hexdigest()[:16]


def sent_keys(rows):
    return {r['source_key'] for r in rows if r.get('source_key')}


def record_issue(rows, issue):
    """Return tracker rows updated for an accepted (sent) issue. Pure; caller persists."""
    rows = [dict(r) for r in rows]
    by_key = {r.get('source_key'): r for r in rows}
    day = issue.local_date.isoformat()
    for lead in issue.leads:
        key = source_key(lead.source_url)
        row = by_key.get(key)
        fields = {'last_issue_included': day, 'headline': lead.headline, 'source_title': lead.source_title,
                  'source_url': lead.source_url, 'agency': lead.get_records.agency, 'action': lead.get_records.action,
                  'newsworthiness': lead.newsworthiness, 'obtainability': lead.obtainability}
        if row is None:
            row = {'lead_id': lead_id(lead.source_url), 'source_key': key, 'first_sent': day, 'times_sent': 0}
            rows.append(row)
            by_key[key] = row
        # A resend of an older issue counts as a send but never rolls the newest snapshot back.
        if day >= row.get('last_issue_included', ''):
            row.update(fields)
        row['first_sent'] = min(row.get('first_sent', day), day)
        row['times_sent'] = row.get('times_sent', 0) + 1
    return rows
