"""Optional subscription list: fetched at send time, validated, never replaced by a fallback list."""
from dataclasses import replace
from datetime import date, datetime, timezone
import http.server
import json
import threading
import pytest
from support import config, sample_issue
from dochunt.config import ConfigError, config_from_data
from dochunt.delivery import prepare
from dochunt.render import render_issue
from dochunt.subscriptions import SubscriptionError, fetch, validate

ORIGIN = 'https://lists.example.org'


def subscribed_config():
    c = config()
    c.email = replace(c.email, subscription_url=ORIGIN, subscription_domains=['newsroom.example'],
                      recipients=['editor@example.org'])
    return c


def listed(*addresses, manage=ORIGIN + '/'):
    return lambda email_config: validate({'recipients': list(addresses), 'manage_url': manage}, email_config)


def test_the_manage_link_is_in_every_render_so_previews_show_it():
    c = subscribed_config()
    r = render_issue(sample_issue(c, date(2026, 10, 2)), c)
    assert 'href="https://lists.example.org/"' in r.html and 'Manage subscription (opt in or opt out): https://lists.example.org/' in r.text
    assert 'Manage subscription' not in render_issue(sample_issue(), config()).html


def test_scheduled_send_goes_to_the_list_with_a_manage_link():
    c = subscribed_config()
    issue = sample_issue(c, date(2026, 10, 2))
    p = prepare(issue, render_issue(issue, c), c, subscriptions=listed('a@newsroom.example', 'editor@example.org'))
    assert p.recipient_snapshot == ('a@newsroom.example', 'editor@example.org')
    assert p.rendered == dict(vars(render_issue(issue, c)))  # exactly the reviewed preview, link included


def test_test_send_keeps_its_one_recipient_but_shows_the_link():
    c = subscribed_config()
    issue = sample_issue(c, date(2026, 10, 2))
    p = prepare(issue, render_issue(issue, c), c, attempt_id='t1', recipient='editor@example.org',
                subscriptions=listed('a@newsroom.example'))
    assert p.recipient_snapshot == ('editor@example.org',) and 'Manage subscription' in p.rendered['html']


@pytest.mark.parametrize('data,message', [
    ({'recipients': ['x@elsewhere.example'], 'manage_url': ORIGIN + '/'}, 'outside the allowed domains'),
    ({'recipients': ['a@newsroom.example.evil.com'], 'manage_url': ORIGIN + '/'}, 'outside the allowed domains'),
    ({'recipients': [], 'manage_url': ORIGIN + '/'}, 'empty'),
    ({'recipients': ['not an address'], 'manage_url': ORIGIN + '/'}, 'invalid address'),
    ({'recipients': ['a@newsroom.example'], 'manage_url': 'https://phish.example/'}, 'manage link'),
    ({'recipients': ['a@newsroom.example'], 'manage_url': 'http://lists.example.org/'}, 'manage link'),
    ({'recipients': ['a@newsroom.example'], 'manage_url': 'https://lists.example.org\\@evil.example/'}, 'manage link'),
    ({'recipients': ['a@newsroom.example'], 'manage_url': 'https://lists.example.org:443/'}, 'manage link'),
    ({'recipients': 'a@newsroom.example', 'manage_url': ORIGIN + '/'}, 'wrong shape'),
])
def test_bad_lists_stop_delivery(data, message):
    with pytest.raises(SubscriptionError, match=message):
        validate(data, subscribed_config().email)


def test_duplicates_are_removed_case_insensitively():
    out = validate({'recipients': ['A@newsroom.example', 'a@newsroom.example'], 'manage_url': ORIGIN + '/'},
                   subscribed_config().email)
    assert out == ['A@newsroom.example']


def test_an_unreachable_list_never_falls_back_to_the_static_recipients():
    c = subscribed_config()
    issue = sample_issue(c, date(2026, 10, 2))

    def down(email_config):
        raise SubscriptionError('Could not load the subscription list; delivery stopped')
    with pytest.raises(SubscriptionError):
        prepare(issue, render_issue(issue, c), c, subscriptions=down)


def test_fetch_sends_the_token_an_explicit_agent_and_refuses_redirects():
    seen, target_hits = {}, []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            target_hits.append((self.path, self.headers['Authorization']))
            self.send_response(200); self.end_headers()

        def log_message(self, *a):
            pass
    target = http.server.HTTPServer(('127.0.0.1', 0), Target)
    tport = target.server_address[1]

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen['auth'], seen['agent'], seen['path'] = self.headers['Authorization'], self.headers['User-Agent'], self.path
            if self.path == '/recipients':
                body = json.dumps({'recipients': ['a@newsroom.example'], 'manage_url': f'http://127.0.0.1:{port}/'}).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
            else:
                self.send_response(302); self.send_header('Location', f'http://127.0.0.1:{tport}/stolen'); self.end_headers()

        def log_message(self, *a):
            pass
    server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    port = server.server_address[1]
    for s_ in (server, target):
        threading.Thread(target=s_.serve_forever, daemon=True).start()
    try:
        # A local test server uses http; the service and link otherwise match, so the list is accepted.
        email = replace(subscribed_config().email, subscription_url=f'http://127.0.0.1:{port}')
        assert fetch(email, token='tok-123') == ['a@newsroom.example']
        assert seen == {'auth': 'Bearer tok-123', 'agent': 'dochunt/0.2 (subscription list)', 'path': '/recipients'}
        with pytest.raises(SubscriptionError, match='Could not load'):
            fetch(replace(email, subscription_url=f'http://127.0.0.1:{port}/x'), token='tok-123')
        assert seen['path'] == '/x/recipients' and target_hits == []  # the redirect was never followed
    finally:
        server.shutdown(); target.shutdown()


def test_missing_token_stops_before_any_request():
    with pytest.raises(SubscriptionError, match='SUBSCRIPTION_API_TOKEN'):
        fetch(subscribed_config().email, token='', opener=lambda *a, **k: pytest.fail('requested without a token'))


def test_missing_token_fails_before_paid_research(tmp_path, monkeypatch):
    from support import NoCalls
    from dochunt.runtime import run
    from dochunt.state import StateStore
    monkeypatch.setenv('RESEND_API_KEY', 'fixture')
    monkeypatch.delenv('SUBSCRIPTION_API_TOKEN', raising=False)
    with pytest.raises(SubscriptionError, match='SUBSCRIPTION_API_TOKEN'):
        run(subscribed_config(), StateStore(tmp_path / 's', git=False), tmp_path / 'out', agent=NoCalls(), manual=True,
            now_utc=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))


@pytest.mark.parametrize('url,domains', [
    ('http://lists.example.org', ['newsroom.example']),
    ('https://lists.example.org/recipients', ['newsroom.example']),
    ('https://user:pw@lists.example.org', ['newsroom.example']),
    ('https://lists.example.org', []),
    ('https://lists.example.org', ['Newsroom.Example']),
    ('', ['newsroom.example']),
])
def test_subscription_settings_are_validated(url, domains):
    import tomllib
    from pathlib import Path
    data = tomllib.loads(Path('examples/military.toml').read_text())
    data['email'].update(subscription_url=url, subscription_domains=domains)
    with pytest.raises(ConfigError):
        config_from_data(data)


def test_valid_settings_add_the_token_to_the_required_secrets():
    c = subscribed_config()
    assert c.secret_names['subscriptions'] == 'SUBSCRIPTION_API_TOKEN'
    assert 'subscriptions' not in config().secret_names


def test_only_delivery_steps_receive_the_subscription_token():
    from pathlib import Path
    from test_workflow import step
    w = Path('.github/workflows/document-hunt.yml').read_text()
    for name in ('Delivery credential readiness before paid research', 'Deliver with synchronous state publication',
                 'Explicit selected-preview test or resend'):
        assert 'SUBSCRIPTION_API_TOKEN' in step(w, name)
    for name in ('Research', 'Paid AI preview (research only, no email)'):
        assert 'SUBSCRIPTION_API_TOKEN' not in step(w, name)


def test_a_list_outage_fails_before_paid_research(tmp_path, monkeypatch):
    from support import NoCalls
    from dochunt.runtime import run
    from dochunt.state import StateStore
    from dochunt import subscriptions
    monkeypatch.setenv('RESEND_API_KEY', 'fixture')
    monkeypatch.setenv('SUBSCRIPTION_API_TOKEN', 'fixture')
    def down(email_config, **k):
        raise SubscriptionError('Could not load the subscription list; delivery stopped')
    monkeypatch.setattr(subscriptions, 'fetch', down)
    with pytest.raises(SubscriptionError, match='Could not load'):
        run(subscribed_config(), StateStore(tmp_path / 's', git=False), tmp_path / 'out', agent=NoCalls(), manual=True,
            now_utc=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))


def _saved_subscription_send(tmp_path):
    from dataclasses import asdict
    from dochunt.state import StateStore
    c = subscribed_config()
    issue = sample_issue(c, date(2026, 10, 2))
    p = prepare(issue, render_issue(issue, c), c, subscriptions=listed('a@newsroom.example', 'b@newsroom.example'))
    assert p.subscription_url == ORIGIN and p.wire == 2
    store = StateStore(tmp_path, git=False)
    store.save({'attempts': {p.attempt_key: {'prepared': asdict(p), 'status': 'pending'}}})
    return c, p, store


def test_a_retry_stops_if_a_saved_recipient_has_since_opted_out(tmp_path, monkeypatch):
    from test_delivery import Fake
    from dochunt import subscriptions
    from dochunt.delivery import send
    from dochunt.runtime import recheck_for
    c, p, store = _saved_subscription_send(tmp_path)
    t = Fake()
    monkeypatch.setattr(subscriptions, 'fetch', lambda e, **k: ['a@newsroom.example'])
    with pytest.raises(SubscriptionError, match='no longer subscribed'):
        send(p, store, t, recheck=recheck_for(c))
    assert not t.calls and store.read()['attempts'][p.attempt_key]['status'] == 'pending'
    monkeypatch.setattr(subscriptions, 'fetch', lambda e, **k: ['a@newsroom.example', 'b@newsroom.example', 'c@newsroom.example'])
    send(p, store, t, recheck=recheck_for(c))
    assert len(t.calls) == 1 and t.calls[0].recipient_snapshot == p.recipient_snapshot  # same payload, same key


def test_a_retry_is_checked_against_the_list_it_was_sent_to(tmp_path):
    from test_delivery import Fake
    from dochunt.delivery import send
    from dochunt.runtime import recheck_for
    c, p, store = _saved_subscription_send(tmp_path)
    static = config()  # subscription removed from the settings since the send was saved
    with pytest.raises(SubscriptionError, match='cannot be matched'):
        send(p, store, Fake(), recheck=recheck_for(static))
    moved = subscribed_config(); moved.email = replace(moved.email, subscription_url='https://other.example.org')
    with pytest.raises(SubscriptionError, match='cannot be matched'):
        send(p, store, Fake(), recheck=recheck_for(moved))
    with pytest.raises(RuntimeError, match='re-checked'):
        send(p, store, Fake())  # no checker at all: never silently retried


@pytest.mark.parametrize('count,ok', [(100, True), (101, False)])
def test_resend_recipient_limit_is_exact_and_enforced_before_any_state(count, ok):
    c = subscribed_config()
    issue = sample_issue(c, date(2026, 10, 2))
    readers = [f'r{i}@newsroom.example' for i in range(count)]
    if ok:
        assert len(prepare(issue, render_issue(issue, c), c, subscriptions=lambda e: readers).recipient_snapshot) == 100
    else:
        with pytest.raises(ValueError, match='at most 100'):
            prepare(issue, render_issue(issue, c), c, subscriptions=lambda e: readers)


def test_an_oversized_list_fails_readiness_before_paid_research():
    from dochunt.runtime import subscription_readiness
    with pytest.raises(ValueError, match='at most 100'):
        subscription_readiness(subscribed_config(), fetcher=lambda e: [f'r{i}@newsroom.example' for i in range(101)])
    subscription_readiness(subscribed_config(), fetcher=lambda e: ['a@newsroom.example'])


def test_inactive_subscription_settings_do_not_change_the_preview_digest():
    import hashlib
    from dataclasses import asdict
    from dochunt.artifacts import config_digest
    c = config()
    legacy = asdict(c); legacy['email'].pop('subscription_url'); legacy['email'].pop('subscription_domains')
    assert config_digest(c) == hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert config_digest(subscribed_config()) != config_digest(config())


def test_oversized_lists_are_rejected_without_scanning_everything():
    from dochunt import subscriptions
    huge = [f'r{i}@newsroom.example' for i in range(subscriptions.MAX_RECIPIENTS + 1)] + ['not-an-address'] * 10
    with pytest.raises(SubscriptionError, match='unexpectedly large'):
        validate({'recipients': huge, 'manage_url': ORIGIN + '/'}, subscribed_config().email)


def test_a_trailing_slash_is_the_same_list(tmp_path, monkeypatch):
    from test_delivery import Fake
    from dochunt import subscriptions
    from dochunt.delivery import send
    from dochunt.runtime import recheck_for
    c, p, store = _saved_subscription_send(tmp_path)
    slashed = subscribed_config(); slashed.email = replace(slashed.email, subscription_url=ORIGIN + '/')
    monkeypatch.setattr(subscriptions, 'fetch', lambda e, **k: ['a@newsroom.example', 'b@newsroom.example'])
    t = Fake(); send(p, store, t, recheck=recheck_for(slashed))
    assert len(t.calls) == 1


def test_a_send_without_a_recorded_list_is_not_retried_once_subscriptions_are_on(tmp_path):
    from dataclasses import asdict
    from test_delivery import Fake, prepared as static_prepared
    from dochunt.delivery import send
    from dochunt.runtime import recheck_for
    from dochunt.state import StateStore
    p = static_prepared()
    saved = asdict(p); saved.pop('subscription_url'); saved.pop('wire')  # as saved by an older version
    store = StateStore(tmp_path, git=False)
    store.save({'attempts': {p.attempt_key: {'prepared': saved, 'status': 'pending'}}})
    t = Fake()
    with pytest.raises(SubscriptionError, match='cannot be matched'):
        send(p, store, t, recheck=recheck_for(subscribed_config()))
    assert not t.calls
    send(p, store, t, recheck=recheck_for(config()))  # still a static installation: retried as before
    assert len(t.calls) == 1
