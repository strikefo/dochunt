"""Bounded provider operations. No delivery imports or email configuration."""
from dataclasses import dataclass, asdict, replace
from importlib.resources import files
from datetime import datetime, timezone
import json
import http.client
import socket
from time import monotonic
from ..budget import BudgetExceeded, bounded_call


MAX_PROVIDER_WAIT_SECONDS = 180


class CapabilityError(RuntimeError): pass
class ProviderError(RuntimeError): pass
class TransientError(ProviderError):
    def __init__(self,message,usage_unknown=False):
        super().__init__(message)
        self.usage_unknown=usage_unknown


class FixtureTransport:
    """Explicitly offline: synthetic wire fixtures only, never sends HTTP."""
    offline=True
    def __init__(self,responses,web=True):
        self.responses=iter(responses); self.requests=[]; self.web=web
    def __call__(self,endpoint,headers,payload,timeout):
        self.requests.append(payload)
        try: result=next(self.responses)
        except StopIteration as exc: raise ProviderError('Fixture exhausted') from exc
        if isinstance(result,Exception): raise result
        return result


class HTTPTransport:
    offline=False
    web=True
    def __call__(self,endpoint,headers,payload,timeout):
        host,path=endpoint
        timeout=min(timeout,MAX_PROVIDER_WAIT_SECONDS)
        expires=monotonic()+timeout
        connection=http.client.HTTPSConnection(host,timeout=timeout)
        try:
            connection.connect()
            if monotonic()>=expires: raise TimeoutError('Client deadline before request')
            connection.request('POST',path,json.dumps(payload).encode(),headers)
            response=connection.getresponse()
            body=response.read(4_000_001)
            if len(body)>4_000_000: raise ProviderError('Provider response exceeds size cap')
            if response.status==429: raise TransientError('Provider rate limited')
            if 500 <= response.status <= 599: raise TransientError('Provider server failure',usage_unknown=True)
            if response.status!=200: raise ProviderError(f'Provider HTTP {response.status}')
            try: return json.loads(body)
            except (ValueError,UnicodeError) as exc: raise ProviderError('Invalid provider response') from exc
        except (socket.timeout,OSError,http.client.HTTPException) as exc:
            raise TransientError('Provider network failure',usage_unknown=True) from exc
        finally: connection.close()


class Provider:
    supported_models=()
    request_timeout_seconds=60
    def __init__(self,model,budget,transport=None,api_key=None,*,retriever=None):
        self.model,self.budget=model,budget
        self.transport=transport or HTTPTransport()
        self.api_key=api_key
        self.retriever=retriever
        self.firecrawl_transport=None
        self.firecrawl=None
        self.reasoning_effort='medium'
        self.stage_output_cap=6000
        self.request_reserves={}
        self.source_receipts=[]
        self.tool_limitations=[]
        self.native_retrieved_at=None
        self.native_receipts={}
        self.research_as_of=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

    def preflight(self):
        if not getattr(self.transport,'web',False): raise CapabilityError('Native web capability required')
        if not isinstance(self.transport,FixtureTransport):
            if self.model not in self.supported_models: raise CapabilityError('Model/native web combination not verified')
            if not self.api_key: raise CapabilityError('Provider API key must be supplied explicitly')
        self.budget.remaining_seconds

    def _request(self,prompt,rules,count,search_cap=0,fetch_cap=0,*,output_kind=None):
        """One bounded operation, including counted retries and returned usage."""
        failures=0
        while True:
            output_cap=min(self.stage_output_cap,self.budget.remaining('output_tokens'))
            if output_cap<1: raise BudgetExceeded('Output allowance exhausted')
            payload=self.payload(prompt,output_cap,search_cap,fetch_cap,rules)
            payload=self.structured_payload(payload,output_kind)
            self.budget.check(input_tokens=len(json.dumps(payload).encode()),output_tokens=output_cap)
            if self.budget.remaining(count)<=self.request_reserves.get(count,0): raise BudgetExceeded('Reserved downstream request allowance')
            self.budget.consume(**{count:1})
            try:
                timeout=min(self.request_timeout_seconds,self.budget.remaining_seconds)
                wire=bounded_call(self.transport,timeout,self.endpoint,self.headers(),payload,timeout)
            except TimeoutError as exc:
                self.budget.blocked=True
                raise ProviderError('Provider client deadline; paid usage unknown') from exc
            except TransientError as exc:
                # Rejected rate-limit requests can retry. Unknown paid usage cannot.
                if exc.usage_unknown:
                    self.budget.blocked=True
                    raise
                if failures>=self.budget.limits.transient_retries: raise
                failures+=1
                continue
            except ProviderError:
                # A discarded HTTP response may already have incurred paid usage.
                # Only explicitly classified rejected attempts above may retry.
                self.budget.blocked=True
                raise
            self.native_processed_counts=None
            try: text,usage,urls=self.normalize(wire)
            except ProviderError:
                self.budget.blocked=True
                raise
            self.budget.record_returned(**usage)
            # Ignored attempts can remain conservatively counted in cumulative
            # usage; native per-request caps apply to actually processed calls.
            processed=self.native_processed_counts or usage
            if (processed['searches']>search_cap or processed['fetches']>fetch_cap or usage['output_tokens']>output_cap
                    or processed['searches']+processed['fetches']>payload.get('max_tool_calls',search_cap+fetch_cap)):
                self.budget.blocked=True
                raise ProviderError('Provider exceeded native tool cap')
            if wire.get('status', 'completed')!='completed' or wire.get('stop_reason','end_turn')!='end_turn':
                raise ProviderError('Provider output incomplete; no successful research claimed')
            if search_cap and usage['searches']:
                self.native_retrieved_at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
                for url in urls: self.native_receipts.setdefault(url,self.native_retrieved_at)
            return text,urls if search_cap or fetch_cap else set(),usage

    def structured_payload(self,payload,output_kind):
        return payload

    def headers(self): raise NotImplementedError
    def payload(self,*args): raise NotImplementedError
    def normalize(self,*args): raise NotImplementedError
