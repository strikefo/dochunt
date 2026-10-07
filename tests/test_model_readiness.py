"""Operator model readiness is offline; generic injected fixtures remain supported."""
from dataclasses import asdict
from pathlib import Path
import pytest
from dochunt.config import load_config
from dochunt.cli import main
from dochunt.github_setup import serialize_config
from dochunt.setup_server import form_config


@pytest.mark.parametrize('field,value', [('model','SENSITIVE-typo'),('reviewer_model','claude-sonnet-5-5')])
def test_operator_validation_rejects_models_without_credentials_or_requests(tmp_path,monkeypatch,capsys,field,value):
    from dochunt.providers.base import HTTPTransport
    monkeypatch.setattr(HTTPTransport,'__call__',lambda *a,**k: pytest.fail('HTTP invoked'))
    for name in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','FIRECRAWL_API_KEY'):
        monkeypatch.delenv(name,raising=False)
    config=load_config(Path('examples/education.toml')); setattr(config.ai,field,value)
    path=tmp_path/'newsroom.toml'; path.write_text(serialize_config(config))
    with pytest.raises(SystemExit) as exc: main(['validate-config','--config',str(path)])
    assert exc.value.code==2
    error=capsys.readouterr().err
    assert 'ai.'+field in error and 'gpt-6.1-sol' in error
    assert value not in error


@pytest.mark.parametrize('provider,model,reviewer',[
    ('openai','gpt-6.1-sol','gpt-6-astra'),
    ('anthropic','claude-sonnet-5-5','claude-opus-5-5'),
    ('anthropic','claude-haiku-4-5',''),
    ('anthropic','claude-haiku-4-5-20251001',''),
])
def test_supported_models_validate_without_credentials(tmp_path,provider,model,reviewer):
    config=load_config(Path('examples/education.toml'))
    config.ai.provider=provider; config.ai.model=model; config.ai.reviewer_model=reviewer
    path=tmp_path/'newsroom.toml'; path.write_text(serialize_config(config))
    assert main(['validate-config','--config',str(path)])==0
    assert form_config(asdict(config),config).ai.model==model


def test_generic_config_still_loads_for_injected_fixture(tmp_path):
    config=load_config(Path('examples/education.toml')); config.ai.model='fixture-only'
    path=tmp_path/'newsroom.toml'; path.write_text(serialize_config(config))
    assert load_config(path).ai.model=='fixture-only'
