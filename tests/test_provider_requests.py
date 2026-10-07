"""Provider request safety: accounting, retries, caps and failures on the path the engine uses.

All transports are offline fixtures. These replace the old pipeline's contract tests and
keep the same guarantees for Provider._request.
"""
import pytest

from dochunt.budget import Budget, BudgetExceeded
from dochunt.models import Limits
from dochunt.providers.anthropic import AnthropicProvider
from dochunt.providers.base import CapabilityError, FixtureTransport, HTTPTransport, ProviderError, TransientError
from dochunt.providers.openai import OpenAIProvider


def ok(text='{}', input_tokens=100, output_tokens=50, status='completed'):
    return {'status': status, 'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]}


def openai(responses, **limits):
    return OpenAIProvider('gpt-6.1-sol', Budget(Limits(**limits)), FixtureTransport(responses))


def test_successful_request_records_returned_usage():
    p = openai([ok(input_tokens=1234, output_tokens=56)])
    text, _, usage = p._request('prompt', 'rules', 'research_requests')
    assert text == '{}' and p.budget.usage.input_tokens == 1234 and p.budget.usage.output_tokens == 56
    assert p.budget.usage.research_requests == 1


def test_unknown_paid_usage_blocks_the_budget_and_never_retries():
    p = openai([TransientError('timeout after dispatch', usage_unknown=True), ok()])
    with pytest.raises(TransientError):
        p._request('prompt', 'rules', 'research_requests')
    assert p.budget.blocked and len(p.transport.requests) == 1
    with pytest.raises(BudgetExceeded):
        p._request('prompt', 'rules', 'research_requests')


def test_rejected_attempts_retry_up_to_the_limit_then_stop():
    p = openai([TransientError('rate limited'), TransientError('rate limited'), TransientError('rate limited'), ok()],
               transient_retries=2)
    with pytest.raises(TransientError):
        p._request('prompt', 'rules', 'research_requests')
    assert len(p.transport.requests) == 3 and not p.budget.blocked


def test_incomplete_output_is_accounted_and_not_success():
    p = openai([ok(status='incomplete', input_tokens=500, output_tokens=40)])
    with pytest.raises(ProviderError):
        p._request('prompt', 'rules', 'research_requests')
    assert p.budget.usage.input_tokens == 500  # paid usage is still recorded


def test_hidden_input_overrun_blocks_the_next_request():
    # The provider reports more (hidden) input than the allowance: recorded, then no more calls.
    p = openai([ok(input_tokens=12_000), ok()], input_tokens=10_000)
    with pytest.raises(BudgetExceeded):
        p._request('prompt', 'rules', 'research_requests')
    assert p.budget.usage.input_tokens == 12_000
    with pytest.raises(BudgetExceeded):
        p._request('prompt', 'rules', 'research_requests')
    assert len(p.transport.requests) == 1


def test_payload_larger_than_the_input_allowance_is_refused_before_dispatch():
    p = openai([ok()], input_tokens=1_000)
    with pytest.raises(BudgetExceeded):
        p._request('x' * 5_000, 'rules', 'research_requests')
    assert p.transport.requests == []


def test_transport_failure_after_dispatch_blocks_the_shared_budget():
    p = openai([ProviderError('HTTP 500 body discarded')])
    with pytest.raises(ProviderError):
        p._request('prompt', 'rules', 'research_requests')
    assert p.budget.blocked


def test_native_tool_cap_is_enforced():
    calls = [{'type': 'web_search_call', 'id': f'ws{i}', 'status': 'completed', 'action': {'type': 'search', 'sources': []}}
             for i in range(3)]
    wire = dict(ok(), output=calls + ok()['output'])
    p = openai([wire])
    with pytest.raises(ProviderError):
        p._request('prompt', 'rules', 'research_requests', 1, 1)
    assert p.budget.blocked


def test_claude_cache_tokens_count_as_input():
    wire = {'content': [{'type': 'text', 'text': '{}'}], 'stop_reason': 'end_turn',
            'usage': {'input_tokens': 100, 'output_tokens': 10, 'cache_creation_input_tokens': 900, 'cache_read_input_tokens': 50}}
    p = AnthropicProvider('claude-sonnet-5-5', Budget(Limits()), FixtureTransport([wire]))
    p._request('prompt', 'rules', 'research_requests')
    assert p.budget.usage.input_tokens == 1050


def test_preflight_requires_web_capable_transport_supported_model_and_key():
    class NoWeb:
        web = False
    with pytest.raises(CapabilityError):
        OpenAIProvider('gpt-6.1-sol', Budget(Limits()), NoWeb()).preflight()
    with pytest.raises(CapabilityError):
        OpenAIProvider('unsupported-model', Budget(Limits()), HTTPTransport(), api_key='k').preflight()
    with pytest.raises(CapabilityError):
        OpenAIProvider('gpt-6.1-sol', Budget(Limits()), HTTPTransport()).preflight()
