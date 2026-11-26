"""Shared offline fixtures. Everything is fictional; no network, no keys, no agent CLI."""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path

from dochunt.agents import AgentError, AgentResult
from dochunt.config import load_config
from dochunt.hunt import fixture, synthetic_issue


def config(path='examples/education.toml'):
    return load_config(Path(path))


def sample_data(name='sample'):
    return deepcopy(fixture(name))


def sample_issue(cfg=None, local_date=date(2026, 10, 2), name='sample'):
    return synthetic_issue(cfg or config(), local_date, name)[0]


class FakeAgent:
    """Replays queued agent answers (dicts, raw strings or AgentError) and records each prompt."""
    def __init__(self, *answers):
        self.answers = list(answers) or [sample_data()]
        self.prompts = []
        self.timeouts = []

    def __call__(self, config, prompt, timeout=None):
        self.prompts.append(prompt)
        self.timeouts.append(timeout)
        if not self.answers:
            raise AssertionError('agent called more times than expected')
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        raw = answer if isinstance(answer, str) else json.dumps(answer)
        return AgentResult(answer, raw, 1.0, {'input_tokens': 100, 'output_tokens': 50})


def no_fetch(url):
    return None


class NoCalls:
    """An agent or transport that fails the test if anything uses it (no paid call allowed)."""
    def __call__(self, *args, **kwargs):
        import pytest
        pytest.fail('agent used where no paid call is allowed')

    def __getattr__(self, name):
        import pytest
        pytest.fail(f'used where no call is allowed: {name}')


__all__ = ['AgentError', 'FakeAgent', 'NoCalls', 'config', 'no_fetch', 'sample_data', 'sample_issue']
