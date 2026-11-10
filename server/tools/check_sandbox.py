"""容器权限基线自检：非 root / 只读根 / tmpfs / pip 可用。

用法：`python tools/check_sandbox.py`（需要 docker 可用）

换过 `APP_AGENT_PRO_RUN_AS` / `APP_AGENT_PRO_READONLY_ROOT` 之后跑一次，
确认"隔离真的生效"而不是"配置写了但没传进去"——这类问题在 API 层看不出来，
只会表现为某天模型把容器里的系统文件改了。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.agent_deep_pro import ProBackend, docker_ready
from app.services.capabilities import AgentContext

ok, note = docker_ready()
print("docker:", ok, note[:60])
ctx = AgentContext(workspace_id="ws-sandbox", owner_id="u-sandbox")
be = ProBackend(str(Path("data/projects/ws-sandbox").resolve()), ctx, mode="container")
print("mode:", be.mode)
checks = {
    "uid": "id -u",
    "tmp可写": "echo hi > /tmp/a.txt && cat /tmp/a.txt",
    "根只读": "touch /etc/should_fail 2>&1 || echo BLOCKED",
    "python": "python3 -c 'print(6*7)'",
    "pip用户安装": "pip install --quiet --no-input six 2>&1 | tail -3; python3 -c 'import six; print(\"six ok\", six.__version__)'",
}
for name, cmd in checks.items():
    r = be.execute(cmd, timeout=180)
    print(f"--- {name} (exit={r.exit_code}) ---")
    print((r.output or "").strip()[:600])
