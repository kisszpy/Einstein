"""模型注册表：解析优先级、可见性、密钥不回显、删除后引用回退。

这几条对应最容易出错的地方：
- 「没建模型」和「建了但停用」都必须安静地回落到 .env，而不是抛 500；
- 默认模型至多一行（两个默认等于没有默认）；
- 列出来的 payload 不能带明文密钥；
- 删掉模型后，引用它的专家要回到「跟随默认」，不能指向一个不存在的 id。
"""

from __future__ import annotations

import pytest

from app.services import models


@pytest.fixture(autouse=True)
def _isolate():
    """每个用例前清空 models 表并清缓存（测试库是 sqlite，走真实落库路径）。"""
    from app.db.engine import get_db
    from app.db.schema import migrate

    migrate(get_db())
    models._table().delete_where("1=1")
    models._invalidate()
    yield
    models._table().delete_where("1=1")
    models._invalidate()


def _create(**kw) -> dict:
    base = dict(
        name="本地 27B",
        base_url="http://127.0.0.1:8080/v1/",
        model="qwen3.6-27b",
        api_key="sk-secret-abcd",
        workspace_id="ws1",
        owner_id="u1",
    )
    base.update(kw)
    return models.create_model(**base)


def test_resolve_falls_back_to_env_when_no_registry_row():
    resolved = models.resolve("")
    assert resolved.source == "env"
    assert resolved.model_id == ""


def test_default_model_wins_over_env():
    entry = _create(is_default=True)
    resolved = models.resolve("")
    assert resolved.source == "registry"
    assert resolved.model_id == entry["id"]
    # url 末尾斜杠被归一掉，避免拼出 //chat/completions
    assert resolved.base_url == "http://127.0.0.1:8080/v1"
    assert resolved.api_key == "sk-secret-abcd"


def test_only_one_default_per_workspace():
    first = _create(name="A", is_default=True)
    second = _create(name="B", is_default=True)
    rows = {m["id"]: m for m in models.list_models("ws1", "u1")}
    assert rows[first["id"]]["is_default"] is False
    assert rows[second["id"]]["is_default"] is True
    assert models.resolve("").model_id == second["id"]


def test_explicit_id_beats_default():
    _create(name="A", is_default=True)
    other = _create(name="B", model="other-model")
    assert models.resolve(other["id"]).model == "other-model"


def test_disabled_model_falls_back_instead_of_raising():
    entry = _create(is_default=True)
    models.update_model(entry["id"], {"enabled": False})
    # 停用的默认模型不该让整条链路 500，安静回落
    assert models.resolve("").source == "env"
    assert models.resolve(entry["id"]).source == "env"


def test_payload_never_leaks_key():
    entry = _create()
    payload = models.model_payload(entry)
    assert payload["has_key"] is True
    assert "sk-secret-abcd" not in str(payload)
    assert payload["key_hint"].endswith("abcd")
    # 落盘的是密文，不是明文
    assert entry["api_key"].startswith("enc:v2:")


def test_update_with_empty_key_keeps_existing():
    entry = _create()
    models.update_model(entry["id"], {"api_key": "", "name": "改名"})
    assert models.get_model(entry["id"])["name"] == "改名"
    assert models.resolve(entry["id"]).api_key == "sk-secret-abcd"


def test_clear_key_explicitly():
    entry = _create()
    models.update_model(entry["id"], {"clear_key": True})
    assert not models.get_model(entry["id"])["api_key"]


def test_visibility_matrix():
    mine = _create(name="我的")
    _create(name="同事的", owner_id="u2")
    _create(name="别的空间", workspace_id="ws2", owner_id="u1")

    visible = {m["id"] for m in models.list_models("ws1", "u1")}
    assert visible == {mine["id"]}


def test_duplicate_name_rejected():
    _create(name="同名")
    with pytest.raises(ValueError):
        _create(name="同名")


def test_bad_base_url_rejected():
    with pytest.raises(ValueError):
        _create(base_url="ftp://example.com")


def test_delete_returns_true_only_once():
    entry = _create()
    assert models.delete_model(entry["id"]) is True
    assert models.delete_model(entry["id"]) is False
    assert models.resolve("").source == "env"


def test_cache_invalidated_on_write():
    """写操作后立刻可见：配置不是秒级强一致场景，但本进程内不该看到旧值。"""
    assert models.resolve("").source == "env"
    entry = _create(is_default=True)
    assert models.resolve("").model_id == entry["id"]
