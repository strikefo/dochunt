"""Reasoning Responses accounting and fail-closed stage boundaries."""
import pytest
from dochunt.budget import Budget, BudgetExceeded
from dochunt.models import Limits
from dochunt.providers.base import FixtureTransport, CapabilityError, ProviderError, TransientError
from dochunt.providers.openai import OpenAIProvider


def wire(text, search=False, urls=()):
    output=[]
    if search: output.append({'type':'web_search_call','id':'ws-fixture','status':'completed','action':{'type':'search','sources':[{'url':u} for u in urls]}})
    output.append({'type':'message','content':[{'type':'output_text','text':text}]})
    return {'status':'completed','output':output,'usage':{'input_tokens':100,'output_tokens':50}}


def test_older_nonreasoning_models_are_not_silently_downgraded():
    for model in ('gpt-4.1','gpt-4.1-mini'):
        p=OpenAIProvider(model,Budget(Limits()),api_key='synthetic-unused')
        with pytest.raises(CapabilityError): p.preflight()
        assert p.budget.usage.research_requests==0


def test_reasoning_items_are_accounted_and_never_used_as_evidence():
    p=OpenAIProvider('gpt-6.1-sol',Budget(Limits()),FixtureTransport([]))
    data=wire('Output',True,['https://example.org/report'])
    data['output'].insert(0,{'type':'reasoning','summary':[{'type':'summary_text','text':'Not evidence'}]})
    data['usage']['output_tokens']=777
    text,counts,urls=p.normalize(data)
    assert text=='Output' and counts['output_tokens']==777 and urls=={'https://example.org/report'}






PUBLIC='https://example.org/current-report'
UNFINISHED='https://example.org/unread-document'


def native_call(identifier,kind,status='completed',url=PUBLIC):
    action={'type':kind}
    if kind=='search': action['sources']=[{'url':PUBLIC}]
    else: action['url']=url
    return {'type':'web_search_call','id':identifier,'status':status,'action':action}


def capped_response(calls,status='completed',echo=2):
    response=wire('Useful primary report findings')
    response.update(status=status,max_tool_calls=echo)
    response['output']=calls+response['output']
    return response


def request_capped(response):
    t=FixtureTransport([response]); b=Budget(Limits(),lambda:0)
    p=OpenAIProvider('gpt-6.1-sol',b,t)
    return p,t,b


def test_completed_response_can_ignore_only_cap_trailing_unfinished_attempt():
    response=capped_response([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','searching',UNFINISHED)])
    response['output'][-1]['content'][0]['annotations']=[{'type':'url_citation','url':UNFINISHED}]
    p,t,b=request_capped(response)
    text,urls,usage=p._request('brief','rules','research_requests',2,2)
    assert text=='Useful primary report findings' and urls=={PUBLIC}
    assert usage['searches']==1 and usage['fetches']==2  # Conservative attempted usage.
    assert b.usage.searches==1 and b.usage.fetches==2 and not b.blocked
    assert p.native_processed_counts=={'searches':1,'fetches':1}
    assert any('ignored' in note and 'cap' in note for note in p.tool_limitations)


@pytest.mark.parametrize('calls,status,echo',[
    ([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page')],'completed',2),
    ([native_call('ws1','search'),native_call('ws2','open_page','searching')],'completed',2),
    ([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','mystery')],'completed',2),
    ([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','searching')],'incomplete',2),
    ([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','searching')],'completed',3),
    ([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','searching'),native_call('ws4','open_page')],'completed',2),
    ([native_call('ws1','search'),native_call('ws1','open_page'),native_call('ws3','open_page','searching')],'completed',2),
    ([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','failed')],'completed',2),
])
def test_completed_overruns_and_ambiguous_native_attempts_still_fail_closed(calls,status,echo):
    p,t,b=request_capped(capped_response(calls,status,echo))
    with pytest.raises(ProviderError): p._request('brief','rules','research_requests',2,2)
    assert b.blocked and len(t.requests)==1
    with pytest.raises(BudgetExceeded): p._request('brief','rules','research_requests',2,2)


def test_ignored_duplicate_url_does_not_remove_previously_completed_source():
    p,t,b=request_capped(capped_response([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','searching')]))
    assert p._request('brief','rules','research_requests',2,2)[1]=={PUBLIC}


def test_unfinished_attempt_without_application_request_cap_is_ambiguous():
    p=OpenAIProvider('gpt-6.1-sol',Budget(Limits()),FixtureTransport([]))
    with pytest.raises(ProviderError): p.normalize(capped_response([native_call('ws1','search'),native_call('ws2','open_page'),native_call('ws3','open_page','searching')]))
