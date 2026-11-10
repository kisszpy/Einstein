"""验证：专家挂载的知识库在智能体链路生效（与 chat 链路语义统一）。

之前 agent.py 只用 req.kb_ids，expert.kb_ids 被静默丢弃——同一个专家在
chat 里 KB 生效、在智能体里失效。修复后 _resolve_mounts 把专家 kb_ids
带出并与请求合并，owned_kb_ids 依旧收敛到本工作区（防越权）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.agent import AgentRunRequest, _resolve_mounts  # noqa: E402
from app.services import experts as experts_mod  # noqa: E402


class FakeStore:
    """只模拟 owned_kb_ids 的收敛语义，其余方法不需要。"""

    def __init__(self, owned: set[str]) -> None:
        self.owned = owned

    def owned_kb_ids(self, workspace_id: str, kb_ids: list[str]) -> list[str]:
        return [i for i in kb_ids if i in self.owned]


def main() -> None:
    WS = "ws-test"
    expert_kb = "kb-expert-1"
    req_kb = "kb-req-1"

    # 1) 专家带 kb_ids，_resolve_mounts 必须原样带出
    store = experts_mod.ExpertStore()
    expert = store.add(
        name="KB专家",
        persona="测试人设",
        kb_ids=[expert_kb],
        skill_ids=[],
        temperature=0.5,
        workspace_id=WS,
        owner_id="",  # 预置专家：全员可见
    )
    orig_get = experts_mod.get_expert_store
    experts_mod.get_expert_store = lambda: store

    req = AgentRunRequest(task="测试任务", expert_id=expert.id, kb_ids=[req_kb])
    persona, pinned, temperature, expert_kb_ids = _resolve_mounts(WS, "u-test", req)
    assert expert_kb_ids == [expert_kb], f"专家 kb_ids 丢失: {expert_kb_ids}"
    assert "测试人设" in persona and temperature == 0.5
    print(f"PASS-1 专家 kb_ids 带出: {expert_kb_ids}")

    # 2) 合并后 owned_kb_ids 收敛：本工作区拥有的保留，越权的被丢
    kb_ids = FakeStore({expert_kb, req_kb}).owned_kb_ids(WS, [*req.kb_ids, *expert_kb_ids])
    assert sorted(kb_ids) == [expert_kb, req_kb], f"合并结果错误: {kb_ids}"
    kb_ids_leak = FakeStore({req_kb}).owned_kb_ids(WS, [*req.kb_ids, *expert_kb_ids])
    assert kb_ids_leak == [req_kb], f"越权 kb 未被过滤: {kb_ids_leak}"
    print(f"PASS-2 合并收敛: {kb_ids}；越权过滤: {kb_ids_leak}")

    # 3) 跨工作区专家不生效（防探测），kb_ids 不带出
    other = store.add(name="别处专家", kb_ids=[expert_kb], workspace_id="ws-other", owner_id="")
    req2 = AgentRunRequest(task="x", expert_id=other.id, kb_ids=[req_kb])
    persona2, _, _, expert_kb2 = _resolve_mounts(WS, "u-test", req2)
    assert expert_kb2 == [] and persona2 == "", f"跨工作区专家泄漏: {expert_kb2!r}"
    print("PASS-3 跨工作区专家忽略")

    experts_mod.get_expert_store = orig_get
    print("\nPASS: 专家 KB 在智能体链路与 chat 链路语义一致")


if __name__ == "__main__":
    main()
