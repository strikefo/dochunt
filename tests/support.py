"""Shared offline fixtures: a fictional newsroom site served to the real watch-mode engine.

Everything is fictional (Example County). Model outputs are the bundled canned outputs
used by the synthetic preview; no network, no keys.
"""
from datetime import date
import hashlib
import json
from importlib.resources import files
from pathlib import Path
import tempfile

from dochunt.budget import Budget
from dochunt.config import load_config
from dochunt.extraction import extract
from dochunt.providers.base import FixtureTransport
from dochunt.providers.openai import OpenAIProvider
from dochunt.retrieval import Retrieved

FEED = 'https://auditor.example/feed.xml'


def fictional():
    return json.loads(files('dochunt').joinpath('fixtures', 'fictional_preview.json').read_text())


def _feed():
    doc = fictional()['sources'][0]
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>Auditor reports</title><item>'
            f'<title>{doc["title"]}</title><link>{doc["url"]}</link><pubDate>Thu, 01 Oct 2026 12:00:00 GMT</pubDate>'
            '</item></channel></rss>').encode()


class Retriever:
    """Serves the fictional feed, audit and records page; anything else is unavailable."""
    def __init__(self):
        sources = fictional()['sources']
        self.pages = {s['url']: s for s in sources}
        self.fetched = []

    def fetch(self, url, *, reserve_fetches=0):
        self.fetched.append(url)
        if url == FEED:
            parsed = extract(_feed(), 'application/rss+xml', url, 5)
            text = ' '.join(s['text'] for s in parsed['sections'])
            return Retrieved(url, text, 'Public document retrieved', '2026-10-01T00:00:00Z',
                             hashlib.sha256(text.encode()).hexdigest(), 'feed', parsed['title'], parsed['sections'], parsed['links'])
        page = self.pages.get(url)
        if page is None:
            return Retrieved(url, None, 'Document inaccessible: HTTP 404')
        return Retrieved(url, page['text'], 'Public document retrieved', page['retrieved_at'],
                         hashlib.sha256(page['text'].encode()).hexdigest(), page['format'], page['title'],
                         [{'locator': 'text', 'text': page['text']}], page['links'])


def wire(data):
    return {'status': 'completed', 'usage': {'input_tokens': 900, 'output_tokens': 300},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(data), 'annotations': []}]}]}


def config(path='examples/education.toml'):
    """A fictional newsroom in watch mode over the fictional site."""
    c = load_config(Path(path))
    c.hunt.mode, c.hunt.playbook = 'watch', ''
    c.hunt.watch = [FEED]
    c.hunt.request_pages = [fictional()['sources'][1]['url']]
    c.hunt.keywords = []
    c.hunt.seen_path = str(Path(tempfile.mkdtemp()) / 'seen.json')
    return c


def anthropic_wire(data):
    return {'content': [{'type': 'text', 'text': json.dumps(data)}], 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 900, 'output_tokens': 300}}


def provider(cfg, outputs=None, kind='openai'):
    """Provider replaying the canned analysis and review, reading the fictional site."""
    outputs = outputs if outputs is not None else fictional()['outputs']
    if kind == 'anthropic':
        from dochunt.providers.anthropic import AnthropicProvider
        p = AnthropicProvider('claude-sonnet-5-5', Budget(cfg.limits), FixtureTransport([anthropic_wire(o) for o in outputs]))
    else:
        p = OpenAIProvider('gpt-6.1-sol', Budget(cfg.limits), FixtureTransport([wire(o) for o in outputs]))
    p.retriever = Retriever()
    return p


def sample_issue(cfg=None, local_date=date(2026, 10, 2)):
    """A real engine issue from the fictional site."""
    from dochunt.engine import hunt_preview
    cfg = cfg or config()
    issue, _ = hunt_preview(cfg, provider(cfg), tempfile.mkdtemp(), local_date=local_date)
    return issue


class NoCalls:
    """A provider that fails the test if anything uses it (no paid call allowed)."""
    def __getattr__(self, name):
        import pytest
        pytest.fail(f'provider used where no paid call is allowed: {name}')
