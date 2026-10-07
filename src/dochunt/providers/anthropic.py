"""Claude Messages API with basic tools, excluding hidden code execution."""
from .base import Provider, ProviderError


class AnthropicProvider(Provider):
    endpoint=('api.anthropic.com','/v1/messages')
    supported_models=('claude-sonnet-5-5','claude-haiku-4-5-20251001','claude-haiku-4-5','claude-opus-5-5')

    def headers(self):
        return {'x-api-key':self.api_key or 'offline-fixture','anthropic-version':'2023-06-01','Content-Type':'application/json'}

    def payload(self,prompt,output_cap,search_cap,fetch_cap,rules):
        payload={'model':self.model,'max_tokens':output_cap,'system':rules,'messages':[{'role':'user','content':prompt}]}
        if search_cap:
            tools=[{'type':'web_search_20250305','name':'web_search','max_uses':search_cap,'allowed_callers':['direct']}]
            if fetch_cap:
                tools.append({'type':'web_fetch_20250910','name':'web_fetch','max_uses':fetch_cap,'allowed_callers':['direct'],'max_content_tokens':2000,'citations':{'enabled':True}})
            payload['tools']=tools
        return payload

    def normalize(self,wire):
        self.tool_limitations=[]
        self.native_explicit_sources=[]
        try:
            usage=wire['usage']
            for key in ('input_tokens','output_tokens','cache_creation_input_tokens','cache_read_input_tokens'):
                value=usage.get(key,0)
                if type(value) is not int or value<0: raise ProviderError('Invalid token usage')
            counts={'input_tokens':usage['input_tokens']+usage.get('cache_creation_input_tokens',0)+usage.get('cache_read_input_tokens',0),'output_tokens':usage['output_tokens'],'searches':0,'fetches':0}
            text=[]; urls=set(); explicit=[]; tool_counts={'web_search':0,'web_fetch':0}
            for block in wire['content']:
                kind=block['type']
                if kind=='server_tool_use':
                    if block['name'] not in tool_counts: raise ProviderError('Unexpected nested tool')
                    tool_counts[block['name']]+=1
                elif kind in ('web_search_tool_result','web_fetch_tool_result'):
                    content=block['content']
                    if isinstance(content,list):
                        for result in content:
                            if result.get('type')=='web_search_result': urls.add(result['url'])
                            elif result.get('type')=='web_search_tool_result_error': self.tool_limitations.append('Native search limitation: '+str(result.get('error_code','unknown')))
                            else: raise ProviderError('Unknown search result')
                    elif isinstance(content,dict):
                        if content.get('type')=='web_fetch_result':
                            urls.add(content['url']); explicit.append(content['url'])
                        elif content.get('type') in ('web_fetch_tool_result_error','web_search_tool_result_error'): self.tool_limitations.append('Native retrieval limitation: '+str(content.get('error_code','unknown')))
                        else: raise ProviderError('Unknown tool result')
                    else: raise ProviderError('Malformed web result')
                elif kind=='text':
                    text.append(block['text'])
                    explicit.extend(c['url'] for c in block.get('citations',[]) if isinstance(c,dict) and isinstance(c.get('url'),str))
                elif kind in ('thinking','redacted_thinking'): pass  # Counted in total output tokens.
                else: raise ProviderError('Unexpected provider output/tool')
            server=usage.get('server_tool_use',{})
            if not isinstance(server,dict) or any(type(v) is not int or v<0 for v in server.values()): raise ProviderError('Invalid tool accounting')
            if any(k not in ('web_search_requests','web_fetch_requests') and v for k,v in server.items()): raise ProviderError('Unknown nested tool accounting')
            counts['searches']=max(tool_counts['web_search'],server.get('web_search_requests',0))
            counts['fetches']=max(tool_counts['web_fetch'],server.get('web_fetch_requests',0))
            if any(type(v) is not int or v < 0 for v in counts.values()): raise ProviderError('Invalid usage')
            self.native_explicit_sources=[url for url in dict.fromkeys(explicit) if url in urls]
            return '\n'.join(text),counts,urls
        except (KeyError,TypeError,AttributeError) as exc: raise ProviderError('Missing or malformed provider accounting') from exc
