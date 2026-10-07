"""Provider timing and model readiness (no network)."""
from pathlib import Path

import pytest

from dochunt.budget import Budget
from dochunt.config import load_config
from dochunt.models import Limits
from dochunt.providers import create_provider
from dochunt.providers.base import FixtureTransport, HTTPTransport
from dochunt.providers.openai import OpenAIProvider


def response(kind, text, tools=True):
    """A completed OpenAI wire response carrying `text` (the only provider these tests time)."""
    assert kind is OpenAIProvider
    return {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 50},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]}


@pytest.mark.parametrize('path',['newsroom.toml','examples/education.toml','examples/military.toml'])
def test_shipped_models_pass_preflight_without_network(path):
    from dochunt.providers import create_provider
    config=load_config(Path(path))
    provider=create_provider(config.ai.provider,config.ai.model,Budget(Limits(),lambda:0),api_key='synthetic-unused')
    provider.preflight()


@pytest.mark.parametrize('elapsed,expected_timeout',[(0,180),(1458,42)])
def test_reasoning_requests_use_longer_wait_clamped_to_original_deadline(elapsed,expected_timeout):
    from dochunt.providers.openai import OpenAIProvider as ReasoningProvider
    now=[0]; budget=Budget(Limits(),lambda:now[0]); now[0]=elapsed
    timeouts=[]
    class CapturingTransport(FixtureTransport):
        def __call__(self,endpoint,headers,payload,timeout):
            timeouts.append(timeout)
            return super().__call__(endpoint,headers,payload,timeout)
    transport=CapturingTransport([response(OpenAIProvider,'Complete',False)])
    provider=ReasoningProvider('gpt-6.1-sol',budget,transport)
    provider._request('input','rules','research_requests')
    assert timeouts==[expected_timeout] and budget.started==0


def test_http_transport_preserves_extended_reasoning_timeout(monkeypatch):
    from dochunt.providers.base import HTTPTransport
    waits=[]
    class Response:
        status=200
        def read(self,cap): return b'{}'
    class Connection:
        def __init__(self,host,timeout): waits.append(timeout)
        def connect(self): pass
        def request(self,*a,**k): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr('dochunt.providers.base.http.client.HTTPSConnection',Connection)
    HTTPTransport()(('api.openai.com','/v1/responses'),{}, {},180)
    assert waits==[180]
