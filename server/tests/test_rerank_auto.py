"""重排的 auto 档：装了就用，没装就关，不要让用户回来改配置。"""

from __future__ import annotations

import pytest

from app.services import rerank


class _S:
    rerank_provider = "auto"
    rerank_endpoint = ""
    rerank_model = "reranker"
    rerank_api_key = ""
    rerank_model_dir = ""


@pytest.fixture()
def settings(monkeypatch):
    s = _S()
    monkeypatch.setattr("app.services.rerank.get_settings", lambda: s)
    return s


def test_auto_without_anything_is_noop(settings):
    assert isinstance(rerank.get_reranker(), rerank.NoopRerank)


def test_auto_uses_http_when_endpoint_configured(settings):
    settings.rerank_endpoint = "http://127.0.0.1:8080/v1/rerank"
    provider = rerank.get_reranker()
    assert isinstance(provider, rerank.HttpRerank)
    assert provider.name != "none"


def test_explicit_none_stays_off(settings):
    settings.rerank_provider = "none"
    settings.rerank_endpoint = "http://127.0.0.1:8080/v1/rerank"
    assert isinstance(rerank.get_reranker(), rerank.NoopRerank)


def test_http_without_endpoint_falls_back_to_noop(settings):
    settings.rerank_provider = "http"
    assert isinstance(rerank.get_reranker(), rerank.NoopRerank)
