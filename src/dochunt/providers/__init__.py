"""Provider adapters; callers supply only the selected provider's AI credential."""
from .base import CapabilityError, ProviderError
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider


def create_provider(name,model,budget,api_key=None,transport=None):
    providers={'openai':OpenAIProvider,'anthropic':AnthropicProvider}
    if name not in providers: raise CapabilityError('Explicit provider integration required')
    return providers[name](model,budget,transport,api_key)

__all__=['create_provider','CapabilityError','ProviderError']
