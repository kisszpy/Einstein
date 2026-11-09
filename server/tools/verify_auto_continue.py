"""验证步数预算自动续跑：触顶不报错、带进展重入、最终交付。

用桩替换 _iter_events：第一次调用模拟 2 次工具调用后 GraphRecursionError，
第二次调用模拟完成。断言：
1. 中间产出 note 帧（自动续接提示），用户不看到「把任务拆小」类文案；
2. 续跑输入 = 原始任务 + 首轮全部消息 + 续接指令（不重复、不丢计划）；
3. final 帧正常产出。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.services import agent_deep


def _stub(calls: list[dict]):
    """构造一个假 _iter_events：记录每次收到的 messages，按脚本产出事件。"""

    async def fake_iter(agent, payload, ctx, pinned_skills=None, carry=None, thread_id=""):  # noqa: ANN001, ANN003
        if carry is None:
            carry = {}
        # 首次输入是 {"messages": [...]}；续跑/恢复时是 Command，桩里只看 messages
        msgs = payload.get("messages") if isinstance(payload, dict) else []
        calls.append({"messages": list(msgs or []), "carry": carry})
        round_no = len(calls)
        if round_no == 1:
            # 模拟首轮：模型调了工具、写了计划，然后预算触顶
            carry["todos"] = [{"content": "步骤一", "status": "completed"}]
            carry["messages"].append(AIMessage(id="a1", content="", tool_calls=[
                {"id": "t1", "name": "web_search", "args": {"query": "x"}},
            ]))
            carry["messages"].append(ToolMessage(id="s1", tool_call_id="t1", content="结果"))
            yield {"kind": "tool_call", "name": "web_search", "args": "x"}
            yield {"kind": "tool_result", "name": "web_search", "content": "结果", "duration_ms": 5}
            yield {"kind": "plan", "items": [{"content": "步骤一", "status": "completed"}]}
            raise GraphRecursionError("模拟触顶")
        # 模拟续跑完成
        yield {"kind": "__final_text__", "text": "任务完成"}
    return fake_iter


async def main() -> int:
    calls: list[dict] = []
    orig = agent_deep._iter_events
    agent_deep._iter_events = _stub(calls)  # type: ignore[assignment]
    try:
        events = []
        async for ev in agent_deep.run_deep_agent("写一份长报告", [], [], "default"):
            events.append(ev)
    finally:
        agent_deep._iter_events = orig  # type: ignore[assignment]

    fails: list[str] = []
    notes = [e for e in events if e["kind"] == "note"]
    finals = [e for e in events if e["kind"] == "final"]
    if len(calls) != 2:
        fails.append(f"应续跑一次（共 2 次入图），实际 {len(calls)} 次")
    if not notes:
        fails.append("缺少 note 帧（自动续接提示）")
    else:
        text = notes[0].get("content", "")
        if "2/3" not in text:
            fails.append(f"note 文案不含续接序号: {text!r}")
    if not finals or finals[0]["text"] != "任务完成":
        fails.append(f"final 帧异常: {finals}")
    if any("拆小" in str(e) or "拆成多轮" in str(e) for e in events):
        fails.append("事件流里出现把任务推回用户的文案")

    # 续跑输入结构：原始任务 + 首轮消息 + 续接指令
    if len(calls) == 2:
        second = calls[1]["messages"]
        strs = []
        for m in second:
            strs.append(m[1] if isinstance(m, tuple) else getattr(m, "content", ""))
        joined = "|".join(strs)
        if "写一份长报告" not in joined:
            fails.append("续跑输入丢失原始任务")
        if "预算已续接" not in joined and "预算已用完" not in joined:
            fails.append("续跑输入缺少续接指令")
        if not any(getattr(m, "id", None) == "s1" for m in second if not isinstance(m, tuple)):
            fails.append("续跑输入丢失首轮工具结果消息")
        if "预算已续接，这是最后一轮" in joined:
            fails.append("第 2/3 轮不应使用 last=true 文案")
        todos = calls[1].get("carry", {}).get("todos")
        if not todos:
            fails.append("续跑时计划状态（todos）丢失")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS: 触顶自动续跑 2/3 -> 完成；note/final 帧齐全；续跑输入结构正确")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
