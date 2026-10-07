"""OpenAI reasoning Responses API; native discovery is not document proof."""
from .base import Provider, ProviderError, MAX_PROVIDER_WAIT_SECONDS
from .schemas import output_schema
from ..retrieval import document_identity


class OpenAIProvider(Provider):
    endpoint=('api.openai.com','/v1/responses')
    supported_models=('gpt-6.1-sol','gpt-6-astra')
    request_timeout_seconds=MAX_PROVIDER_WAIT_SECONDS
    structured_outputs=True  # strict JSON schema via the Responses API text.format

    def headers(self):
        return {'Authorization':'Bearer '+(self.api_key or 'offline-fixture'),'Content-Type':'application/json'}

    def payload(self,prompt,output_cap,search_cap,fetch_cap,rules):
        self.requested_tool_cap=min(search_cap,fetch_cap) if search_cap else 0
        payload={'model':self.model,'input':prompt,'instructions':rules,'max_output_tokens':output_cap,'store':False}
        if self.model in self.supported_models: payload['reasoning']={'effort':self.reasoning_effort}
        if search_cap:
            if fetch_cap<1: raise ProviderError('No allowance for a possible native fetch action')
            payload.update(tools=[{'type':'web_search','search_context_size':'low'}],tool_choice='required',max_tool_calls=min(search_cap,fetch_cap),include=['web_search_call.action.sources'])
        return payload

    def structured_payload(self,payload,output_kind):
        # Native web calls return prose. All no-tool stages get an explicit
        # application-owned schema, including repairs and independent review.
        if output_kind is not None and 'tools' not in payload:
            payload['text'] = {'format': {'type': 'json_schema',
                'name': 'dochunt_' + output_kind, 'strict': True,
                'schema': output_schema(output_kind,self.budget.limits)}}
        return payload

    def normalize(self,wire):
        self.tool_limitations=[]
        self.native_source_priority=[]
        self.native_explicit_sources=[]
        self.native_processed_counts={'searches':0,'fetches':0}
        cap=getattr(self,'requested_tool_cap',None)
        try:
            if 'max_tool_calls' in wire and wire['max_tool_calls'] is not None:
                echoed=wire['max_tool_calls']
                if type(echoed) is not int or echoed<0 or (cap is not None and echoed!=cap): raise ProviderError('Mismatched native tool cap')
            usage=wire['usage']
            counts={'input_tokens':usage['input_tokens'],'output_tokens':usage['output_tokens'],'searches':0,'fetches':0}
            text=[]; urls=set(); citation_urls=set(); ignored_urls=set(); identifiers=set(); ignored=False
            opened=[]; cited=[]; discovered=[]
            for item in wire['output']:
                if item['type']=='web_search_call':
                    action=item.get('action',{})
                    if action.get('type')=='search': counter='searches'
                    elif action.get('type') in ('open_page','find_in_page'): counter='fetches'
                    else: raise ProviderError('Unknown native web action')
                    identifier=item.get('id')
                    if not isinstance(identifier,str) or not identifier or identifier in identifiers: raise ProviderError('Ambiguous native call identity')
                    identifiers.add(identifier)
                    status=item.get('status')
                    counts[counter]+=1  # Conservatively retain even ignored attempts.
                    call_urls={source['url'] for source in action.get('sources',[]) if 'url' in source}
                    if 'url' in action: call_urls.add(action['url'])
                    if any(not isinstance(url,str) for url in call_urls): raise ProviderError('Malformed native source URL')
                    if status=='completed':
                        if ignored: raise ProviderError('Processed call after unfinished native attempt')
                        self.native_processed_counts[counter]+=1
                        urls.update(call_urls)
                        if action.get('type') in ('open_page','find_in_page') and 'url' in action: opened.append(action['url'])
                        discovered.extend(source['url'] for source in action.get('sources',[]) if 'url' in source)
                    elif status in {'in_progress','searching'}:
                        completed=sum(self.native_processed_counts.values())
                        if wire.get('status')!='completed' or cap is None or cap<1 or completed!=cap:
                            raise ProviderError('Ambiguous unfinished native call')
                        ignored=True
                        ignored_urls.update(call_urls)
                        self.tool_limitations.append('Native retrieval limitation: trailing unfinished attempt ignored after processed tool cap; no source access claimed for that attempt.')
                    else: raise ProviderError('Unknown or failed native call status')
                elif item['type']=='message':
                    for part in item['content']:
                        if part['type']=='output_text':
                            text.append(part['text'])
                            citation_urls.update(a['url'] for a in part.get('annotations',[]) if a['type']=='url_citation')
                            cited.extend(a['url'] for a in part.get('annotations',[]) if a['type']=='url_citation')
                        elif part['type']=='refusal': raise ProviderError('Provider refused request')
                elif item['type']=='reasoning': pass  # Already included in billed output tokens.
                else: raise ProviderError('Unexpected provider output/tool')
            if any(type(v) is not int or v < 0 for v in counts.values()): raise ProviderError('Invalid usage')
            proven=urls | (citation_urls-ignored_urls)
            # A citation may add tracking parameters while a completed native
            # search returned the exact untracked document. Prioritize that
            # already-proven URL, without promoting the unfinished URL or
            # rewriting provenance. Semantic query parameters stay distinct.
            completed={}
            for url in opened+discovered:
                if url not in urls: continue
                try: identity=document_identity(url)
                except ValueError: continue  # PublicRetriever still checks the original URL.
                completed.setdefault(identity,[]).append(url)
            citation_priority=[]
            for url in cited:
                if url in proven: citation_priority.append(url)
                else:
                    try: identity=document_identity(url)
                    except ValueError: continue
                    citation_priority.extend(completed.get(identity,[]))
            self.native_explicit_sources=[url for url in dict.fromkeys(opened+citation_priority) if url in proven]
            self.native_source_priority=[url for url in dict.fromkeys(opened+citation_priority+discovered) if url in proven]
            return '\n'.join(text),counts,proven
        except (KeyError,TypeError,AttributeError) as exc: raise ProviderError('Missing or malformed provider accounting') from exc
