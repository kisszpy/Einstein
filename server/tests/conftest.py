"""pytest 配置与共享夹具。

**为什么测试环境要单独指一遍存储**：默认 `.env` 指着生产 Postgres/Milvus，
测试跑上去会往真实知识库里塞数据、改真实审批记录。这里在导入 app 之前
用环境变量把三个后端都按到本地（sqlite 临时文件 + 内存向量 + 本地队列），
`pytest` 因此可以在任何机器上重复跑，不依赖外部服务。

注意：setenv 必须发生在 `import app.*` 之前——`get_settings()` 有 lru_cache，
晚了一步读到旧配置就再也改不动了（真踩过）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="einstein-tests-"))

os.environ["APP_STORE_BACKEND"] = "sqlite"
os.environ["APP_SQLITE_PATH"] = str(_TMP / "test.db")
os.environ["APP_VECTOR_BACKEND"] = "memory"
os.environ["APP_QUEUE_BACKEND"] = "local"
os.environ["APP_RATELIMIT_ENABLED"] = "false"
os.environ["APP_AGENT_CHECKPOINT"] = "memory"
# 审批默认关闭；需要它的用例自己改配置再清缓存
os.environ["APP_AGENT_APPROVAL_TOOLS"] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _TMP


@pytest.fixture(scope="session", autouse=True)
def _fresh_settings_cache():
    """确保所有模块读到的是测试配置，而不是某次 import 时缓存的旧值。"""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def approval_enabled(monkeypatch):
    """临时打开人工确认（code_run），并让配置缓存失效。"""
    from app.core.config import get_settings

    monkeypatch.setenv("APP_AGENT_APPROVAL_TOOLS", "code_run")
    get_settings.cache_clear()
    yield "code_run"
    get_settings.cache_clear()


@pytest.fixture()
def store():
    """一个隔离的 KB store（sqlite 临时库，每个 session 一份）。"""
    from app.services.store import get_store

    return get_store()
