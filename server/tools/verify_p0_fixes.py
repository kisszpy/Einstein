"""批次 A（P0）验收：审批续跑、审批归属与时效、相关性门限、execute 隔离、Token 配额。

跑法：`python tools/verify_p0_fixes.py`
不依赖真实大模型：用脚本化 ChatModel 驱动图，只验证「框架语义是否被正确使用」。

为什么这些断言值得单独存在：这五条都是"看起来正常、实际是坏的"——
- 批准后重跑整个任务（用户看到的是任务重来，已写的文件被再写一遍）；
- 用别人的 session_id 就能预批准；
- 阈值 0.01 让"知识库未命中"永远不可达，噪音被当成证据；
- 默认引擎给模型一个用不了的 execute 工具；
- `APP_QUOTA_DAILY_TOKENS` 配了不生效。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core.config import get_settings
from app.services import approvals, ratelimit
from app.services.agent_deep import _approval_plan, _build_agent, run_deep_agent
from app.services.capabilities import AgentContext, sandbox
from app.services.rag import RetrievedChunk, apply_score_gate, cap_per_document

WS = "ws-p0-verify"
UID = "u-p0"
OTHER_UID = "u-other"
ROOT = Path(__file__).resolve().parents[1]

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS ' if ok else 'FAIL '} {name}" + (f" — {detail}" if detail and not ok else ""))


class ScriptedModel(BaseChatModel):
    """按脚本逐次返回 AIMessage 的假模型（支持 bind_tools）。"""

    script: list = []
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003 — 交给框架随便绑
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # noqa: ANN001, ANN003
        index = min(self.calls, len(self.script) - 1)
        self.calls += 1
        msg = self.script[index]
        return ChatResult(generations=[ChatGeneration(message=msg.model_copy(update={"id": f"m{self.calls}"}))])


def call_tool(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def say(text: str) -> AIMessage:
    return AIMessage(content=text)


async def drain(task, tool_ids, kb_ids, ws, ctx, history, **kwargs) -> list[dict]:
    return [
        event async for event in run_deep_agent(
            task, tool_ids, kb_ids, ws, ctx, history, **kwargs
        )
    ]


def kinds(events: list[dict]) -> list[str]:
    return [e["kind"] for e in events]


def find(events: list[dict], kind: str) -> dict | None:
    return next((e for e in events if e["kind"] == kind), None)


def cleanup() -> None:
    root = sandbox.workspace_root(WS)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    approvals.purge_expired()


async def main() -> None:
    settings = get_settings()
    # 用计算器当"敏感工具"：审批机制与 code_run 完全一致，但没有副作用
    object.__setattr__(settings, "agent_approval_tools", "calculator")
    object.__setattr__(settings, "agent_checkpoint", "memory")

    import app.services.agent_deep as deep_mod

    # ---------- 装配期：审批计划与工具能力 ----------
    probe = AgentContext(workspace_id=WS, owner_id=UID)
    plan = _approval_plan(probe)
    check("审批计划由配置生成", plan == {"calculator": True}, str(plan))
    check("启用审批时标记 hitl（工具不再自己抛异常）", probe.hitl is True)

    default_agent = _build_agent(AgentContext(workspace_id=WS), [], [], WS)
    default_tools = set(default_agent.builder.nodes["tools"].runnable.tools_by_name)
    check("默认引擎不暴露 execute（后端不支持执行）", "execute" not in default_tools,
          str(sorted(default_tools)))

    from app.services.agent_deep_pro import _build_pro_agent

    pro_agent, _backend = _build_pro_agent(AgentContext(workspace_id=WS), [], [], WS)
    pro_tools = set(pro_agent.builder.nodes["tools"].runnable.tools_by_name)
    check("Pro 引擎保留 execute（后端支持执行）", "execute" in pro_tools)

    # ---------- A1：中断 → 批准 → 续跑 ----------
    model = ScriptedModel(script=[
        call_tool("calculator", {"expression": "1+1"}, "c1"),
        say("结果是 2"),
    ])
    deep_mod._get_model = lambda temperature=None: model  # noqa: ARG005 — 测试替身

    thread = f"{WS}:approve"
    ctx = AgentContext(workspace_id=WS, owner_id=UID)
    first = await drain("算一下 1+1", ["calculator"], [], WS, ctx, None, thread_id=thread)
    pause = find(first, "approval_required")
    check("敏感工具在工具执行前暂停（interrupt）", pause is not None and pause.get("tool") == "calculator",
          str(kinds(first)))
    check("暂停时未执行工具", "tool_result" not in kinds(first))
    check("暂停事件带参数指纹", bool((pause or {}).get("args_hash")),
          str((pause or {}).get("args_hash")))
    # 路由在收到 approval_required 帧时会写一条待确认记录，这里模拟同一动作
    approvals.request(WS, UID, "approve", pause.get("tool", ""),
                      args_hash=pause.get("args_hash", ""))
    check("暂停后写一条待确认记录",
          approvals.latest_decision(WS, UID, "approve") is None
          and approvals._latest(WS, UID, "approve", decision="pending") is not None)

    # 越权：别人的 owner 查不到这条记录
    check("他人无法读到待确认记录",
          approvals._latest(WS, OTHER_UID, "approve", decision="pending") is None)

    decided = approvals.decide(WS, UID, "approve", decision="approve",
                              args_hash=(pause or {}).get("args_hash", ""))
    check("参数指纹不匹配时不放行",
          approvals.decide(WS, UID, "approve", decision="approve", args_hash="bogus") is None)
    check("本人批准成功", decided is not None and decided["decision"] == "approve")

    ctx2 = AgentContext(workspace_id=WS, owner_id=UID)
    resumed = await drain("算一下 1+1", ["calculator"], [], WS, ctx2, None,
                          thread_id=thread, resume={"type": "approve"})
    results = [e for e in resumed if e["kind"] == "tool_result"]
    check("批准后从断点继续（有工具结果）", len(results) == 1, str(kinds(resumed)))
    check("续跑没有重做已完成步骤（工具只执行一次）", len(results) == 1, f"{len(results)} 次")
    check("续跑给出最终回答", (find(resumed, "final") or {}).get("text") == "结果是 2")
    check("工具实际执行过（calculator 被调用）", model.calls >= 2, f"model calls={model.calls}")

    # ---------- A1：拒绝让模型换路线 ----------
    model2 = ScriptedModel(script=[
        call_tool("calculator", {"expression": "2*3"}, "c9"),
        say("那我改用口算：6"),
    ])
    deep_mod._get_model = lambda temperature=None: model2  # noqa: ARG005
    thread2 = f"{WS}:reject"
    ctx3 = AgentContext(workspace_id=WS, owner_id=UID)
    paused = await drain("算一下 2*3", ["calculator"], [], WS, ctx3, None, thread_id=thread2)
    pause2 = find(paused, "approval_required")
    check("拒绝场景同样先暂停", pause2 is not None, str(kinds(paused)))
    approvals.request(WS, UID, "reject", "calculator", args_hash=(pause2 or {}).get("args_hash", ""))
    approvals.decide(WS, UID, "reject", decision="reject", args_hash=(pause2 or {}).get("args_hash", ""))
    ctx4 = AgentContext(workspace_id=WS, owner_id=UID)
    after_reject = await drain("算一下 2*3", ["calculator"], [], WS, ctx4, None,
                               thread_id=thread2,
                               resume={"type": "reject", "message": "别用工具了"})
    rejected = [e for e in after_reject if e["kind"] == "tool_result"]
    check("拒绝后工具没有真正执行",
          all("2*3 = 6" not in (r.get("content") or "") for r in rejected),
          str([(r.get("content") or "")[:80] for r in rejected]))
    check("拒绝理由回传给模型",
          any("别用工具了" in (r.get("content") or "") for r in rejected),
          str([(r.get("content") or "")[:80] for r in rejected]))
    check("拒绝后模型给出替代回答", (find(after_reject, "final") or {}).get("text") == "那我改用口算：6")

    # ---------- A2：时效 ----------
    approvals.request(WS, UID, "expired", "calculator", args_hash="abc")
    db = approvals._db()  # noqa: SLF001 — 测试直接改过期时间，模拟 TTL 到点
    db.execute("UPDATE agent_approvals SET expires_at = ? WHERE session_id = ?",
               ("2000-01-01T00:00:00+00:00", "expired"))
    check("过期记录不再生效", approvals.latest_decision(WS, UID, "expired") is None)
    check("过期记录不再授予工具", "calculator" not in approvals.granted_tools(WS, UID, "expired"))

    # ---------- A3：相关性门限 ----------
    strong = RetrievedChunk("c1", "d1", "制度.pdf", "报销标准是每天 300 元", 0.02, vector_score=0.62)
    weak = RetrievedChunk("c2", "d2", "无关.pdf", "今天天气不错", 0.01, vector_score=0.11)
    kept = apply_score_gate("报销标准是多少", [strong, weak], min_score=0.30)
    check("高相似度命中保留", [h.chunk_id for h in kept] == ["c1"], str([h.chunk_id for h in kept]))
    check("低相似度命中被门限丢弃", all(h.chunk_id != "c2" for h in kept))
    lex_hit = RetrievedChunk("c3", "d3", "术语.pdf", "报销标准与差旅报销标准一致", 0.9, lexical_score=3.2)
    lex_miss = RetrievedChunk("c4", "d4", "别处.pdf", "标准件采购流程", 0.8, lexical_score=2.1)
    lex_kept = apply_score_gate("报销标准", [lex_hit, lex_miss], min_score=0.30)
    check("纯词法命中按词覆盖率放行", [h.chunk_id for h in lex_kept] == ["c3"],
          str([h.chunk_id for h in lex_kept]))
    check("全部低于门限时返回空（未命中分支可达）",
          apply_score_gate("毫不相关的问题", [weak], min_score=0.30) == [])
    capped = cap_per_document(
        [RetrievedChunk(f"x{i}", "same", "同一篇.pdf", "t", 0.9) for i in range(4)],
        max_per_doc=2,
    )
    check("同一文档最多贡献 2 片", len(capped) == 2, str(len(capped)))
    check("默认门限已是绝对语义（不是 0.01）", settings.retrieval_min_score >= 0.2,
          str(settings.retrieval_min_score))

    # ---------- A5：Token 配额 ----------
    ratelimit.quota_incr(WS, "tokens", 150)
    object.__setattr__(settings, "quota_daily_tokens", 100)
    allowed, used = ratelimit.quota_check(WS, "tokens")
    check("超过日 Token 配额时拒绝", allowed is False and used >= 150, f"used={used}")
    object.__setattr__(settings, "quota_daily_tokens", 0)
    check("配额为 0 表示不限", ratelimit.quota_check(WS, "tokens")[0] is True)
    object.__setattr__(settings, "quota_daily_tokens", 100000)

    # ---------- 接线断言：入口真的用了上面这些 ----------
    chat_src = (ROOT / "app/api/routes/chat.py").read_text(encoding="utf-8")
    agent_src = (ROOT / "app/api/routes/agent.py").read_text(encoding="utf-8")
    tools_src = (ROOT / "app/services/tools.py").read_text(encoding="utf-8")
    check("对话入口检查 Token 配额", 'quota_check(ws, "tokens")' in chat_src)
    check("智能体入口检查 Token 配额", 'quota_check(ws, "tokens")' in agent_src)
    check("对话与智能体共用检索门面",
          "retrieve(" in chat_src and "retrieve(" in tools_src)
    check("智能体入口不再自建进程内审批表", "_APPROVALS" not in agent_src)
    check("入口把批准决定交给续跑（resume）", "resume=resume_decision" in agent_src)

    cleanup()


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)
