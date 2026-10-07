"""Issue-level checks before rendering, persistence or delivery."""
from dataclasses import asdict
from hashlib import sha256
import json
import re
from .models import Lead, Evidence


def _digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def lead_identity(lead):
    """Stable trail identity: source, records sought, and named entities."""
    return _digest([lead.source_url,sorted(x.strip().casefold() for x in lead.likely_records),sorted(x.strip().casefold() for x in lead.entities)])


def lead_revision(lead):
    return _digest(lead.factual_hook.strip())


def validate_request_placeholders(draft):
    """No unresolved slot for selecting the records' identity."""
    # An explicit slot for selecting the records' identity is not filing-ready.
    # Personalization/date slots and finite programme requests remain valid.
    for slot in re.findall(r"\[([^]\n]{1,300})\]", draft):
        personal=re.fullmatch(r"\s*(?:insert|select|choose|identify|add|supply)\s+"
            r"(?:requester|your)\s+(?:municipality(?: of residence)?|school district|name|"
            r"address|affiliation|contact details|email(?: address)?|phone(?: number)?|signature)\s*",slot,re.I)
        if personal: continue
        if re.match(r"\s*(?:insert|select|choose|identify|add|supply)\b",slot,re.I) and re.search(
                r"\b(?:findings?|cases?|audits?|contracts?|schools?|districts?|entities|municipalit(?:y|ies))\b|\brecords?\s+(?:identifiers?|ids?|numbers?)\b",slot,re.I):
            raise ValueError('Records request has unresolved substantive identifying placeholders')


def validate_issue(issue, config):
    """Revalidate mutable records before rendering/persistence/delivery."""
    for evidence in issue.evidence: Evidence(**asdict(evidence))
    for lead in issue.leads: Lead(**asdict(lead))
    issue.__post_init__()
    if len(issue.leads)>min(config.editorial.max_final_leads,config.limits.max_final_leads):
        raise ValueError('Final lead cap exceeded')
    from .engine import validate_engine_lead, DEFAULT_ALLOWED_ACRONYMS
    for lead in issue.leads:
        if lead.records_plan.get('engine')!='evidence-v1': raise ValueError('Unsupported lead format')
        if lead.source_url not in {e.url for e in issue.evidence if e.id in lead.evidence_ids}:
            raise ValueError('Lead source must match referenced evidence')
        validate_engine_lead(lead,issue.evidence,(*DEFAULT_ALLOWED_ACRONYMS,*config.hunt.allowed_acronyms))
