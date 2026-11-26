"""Optional subscription list: recipients come from the newsroom's own list service at send time.

Contract: GET <subscription_url>/recipients with `Authorization: Bearer <SUBSCRIPTION_API_TOKEN>`
returns {"recipients": [...], "manage_url": "<subscription_url>/"}. The manage link shown in every
email is built by DocHunt from the configured URL (so it appears in reviewed previews); the service
must return exactly that link. Any failure stops delivery; there is never a fallback to another
list, so an opt-out is always honored.
"""
import json
import os
import re
import urllib.request
from urllib.error import URLError

TOKEN_ENV = 'SUBSCRIPTION_API_TOKEN'
EMAIL_RE = re.compile(r'[^\s@<>()",;:]+@([a-z0-9.-]+\.[a-z]{2,})')
MAX_RECIPIENTS = 500


class SubscriptionError(ValueError):
    """The subscription list could not be used; delivery must stop. Message is safe to print."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(email_config, *, token=None, opener=None):
    """Return the validated recipient list from the configured list service."""
    token = token if token is not None else os.environ.get(TOKEN_ENV, '')
    if not token:
        raise SubscriptionError(f'Missing {TOKEN_ENV} repository secret; delivery stopped')
    base = email_config.subscription_url.rstrip('/')
    request = urllib.request.Request(base + '/recipients', headers={
        'Authorization': f'Bearer {token}', 'Accept': 'application/json',
        # Some edge networks block Python's default agent; send an explicit one.
        'User-Agent': 'dochunt/0.2 (subscription list)'})
    open_url = opener or urllib.request.build_opener(_NoRedirects()).open
    try:
        with open_url(request, timeout=30) as response:
            body = response.read(1_000_001)
    except (URLError, OSError, ValueError) as exc:
        raise SubscriptionError('Could not load the subscription list; delivery stopped') from exc
    if len(body) > 1_000_000:
        raise SubscriptionError('Subscription list response is too large; delivery stopped')
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SubscriptionError('Subscription list returned invalid JSON; delivery stopped') from exc
    return validate(data, email_config)


def manage_link(email_config):
    """The subscription page linked from every email: the configured origin with a trailing slash."""
    return email_config.subscription_url.rstrip('/') + '/' if email_config.subscription_url else ''


def validate(data, email_config):
    if not isinstance(data, dict) or not isinstance(data.get('recipients'), list) or not isinstance(data.get('manage_url'), str):
        raise SubscriptionError('Subscription list response has the wrong shape; delivery stopped')
    if data['manage_url'] != manage_link(email_config):
        # Exact match only: no parsing differences between this check and a reader's browser.
        raise SubscriptionError('Subscription manage link does not match the configured service; delivery stopped')
    allowed_exact = {r.lower() for r in email_config.recipients}
    domains = {d.lower() for d in email_config.subscription_domains}
    out, seen = [], set()
    for address in data['recipients']:
        if not isinstance(address, str):
            raise SubscriptionError('Subscription list contains a non-address; delivery stopped')
        address = address.strip()
        lowered = address.lower()
        match = EMAIL_RE.fullmatch(lowered)
        if not match or len(address) > 254:
            raise SubscriptionError('Subscription list contains an invalid address; delivery stopped')
        if lowered not in allowed_exact and match.group(1) not in domains:
            raise SubscriptionError('Subscription list contains an address outside the allowed domains; delivery stopped')
        if lowered not in seen:
            seen.add(lowered)
            out.append(address)
            if len(out) > MAX_RECIPIENTS:
                raise SubscriptionError('Subscription list is unexpectedly large; delivery stopped')
    if not out:
        raise SubscriptionError('Subscription list is empty; delivery stopped')
    return out


def still_subscribed(email_config, snapshot, *, fetcher=None):
    """Before retrying a saved send: stop if anyone in it has since left the list (or the list is down)."""
    current = {a.lower() for a in (fetcher or fetch)(email_config)}
    gone = [a for a in snapshot if a.lower() not in current]
    if gone:
        raise SubscriptionError(f'{len(gone)} saved recipient(s) are no longer subscribed; the retry was stopped for manual reconciliation')
