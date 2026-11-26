"""Eligibility before paid calls; exact durable payload recovery."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .schedule import due
from .delivery import prepare, restore, send, delivery_readiness, sender_header


def run(config, store, output_dir, *, transport=None, now_utc=None, manual=False, research_only=False,
        delivery_only=False, agent=None, fetch=None):
    clock = now_utc or datetime.now(timezone.utc)
    store.check_head()
    states = store.read()
    decision = due(config, clock, states, manual=manual)
    if not decision.due:
        return decision
    for record in states['attempts'].values():
        saved = record['prepared']
        if not saved['preview_test'] and saved['issue']['issue_key'] == decision.issue_key and record['status'] != 'accepted':
            # An interrupted send for today resumes with the exact saved payload; no new research.
            return decision if research_only else send(restore(saved), store, transport, recheck=recheck_for(config))
    if not research_only:
        sender_header(config.email.sender, config.identity.sender_name)
        delivery_readiness(config.email.provider, transport)
        subscription_readiness(config)
    if not delivery_only:
        from .hunt import preview
        from .agents import run_agent
        from .checks import http_status
        local_date = clock.astimezone(ZoneInfo(config.schedule.timezone)).date()
        preview(config, output_dir, local_date=local_date, synthetic=False, tracker_rows=states['leads'],
                agent=agent or run_agent, fetch=fetch or http_status)
    if research_only:
        return decision
    return deliver_artifacts(config, store, output_dir, transport, decision.issue_key)


def subscription_readiness(config, *, fetcher=None):
    """Before paid research: the recipients a send would use must be loadable and within provider limits."""
    from .delivery import check_recipient_limit
    recipients = list(config.email.recipients)
    if config.email.subscription_url:
        from .subscriptions import fetch
        recipients = (fetcher or fetch)(config.email)
    check_recipient_limit(config.email.provider, recipients)


def recheck_for(config):
    """Re-check a saved send's recipients against the list it was sent to, never the current settings.

    Returns None when neither the settings nor the saved send involve a subscription list.
    """
    from .subscriptions import SubscriptionError, still_subscribed
    current = config.email.subscription_url.rstrip('/')

    def recheck(prepared):
        saved = prepared.subscription_url.rstrip('/')
        if not saved and not current:
            return  # a static list, then and now
        if saved != current:
            # Includes sends saved before their list was recorded: their source can't be confirmed.
            raise SubscriptionError('This saved send cannot be matched to the configured subscription list; '
                                    'the retry was stopped for manual reconciliation')
        still_subscribed(config.email, prepared.recipient_snapshot)
    return recheck


def deliver_artifacts(config, store, output_dir, transport, issue_key):
    # The same manifest-checked loader as reviewed previews: every artifact is hashed and re-rendered.
    from .artifacts import load_preview
    issue, rendered = load_preview(output_dir, config, require_paid=True, require_send=True)
    if issue.issue_key != issue_key:
        raise RuntimeError('Artifacts are not for the current local date')
    return send(prepare(issue, rendered, config), store, transport, recheck=recheck_for(config))
