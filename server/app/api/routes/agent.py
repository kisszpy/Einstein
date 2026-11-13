import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import current_uid, current_workspace
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services import (
    approvals,
    models,
    projects,
    ratelimit,
    run_registry,
    traces,
)
from app.services.agent import chunk_text, run_agent
from app.services.capabilities import AgentContext, sandbox
from app.services.history import get_history_store
from app.services.store import get_store
from app.services.tools import list_tools

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentRunRequest(BaseModel):
    task: str
    tool_ids: list[str] = []
    kb_ids: list[str] = []
    project: str = ""
    # 追问时带上所属会话 id；为空表示新任务（服务端分配）
    session_id: str = ""
    # 技能：留空 = 引擎按任务上下文从技能清单自主挑选；点名 = 强制装载
    skill_ids: list[str] = []
    # 专家人设（可选）：只取 persona + temperature，不携带任何挂载
    expert_id: str = ""
    # 本轮指定模型（注册表 id）；空 = 专家偏好 → 平台默认 → .env
    model_id: str = ""
    # 续跑：批准/拒绝之后接着跑，而不是把整个任务重发一遍
    resume: bool = False
    # 拒绝时给模型的指示（"先跑测试再执行"之类），模型据此换路线
    approval_message: str = ""


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/tools")
def get_tools(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    return list_tools(ws, uid)


@router.get("/runs")
def list_runs(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> list[dict]:
    history = get_history_store()
    return [
        {
            "id": r.id,
            "task": r.task,
            "tool_ids": r.tool_ids,
            "steps": r.steps,
            "answer": r.answer,
            "status": r.status,
            "created_at": r.created_at,
            "project": r.project,
            "artifacts": r.artifacts,
            "session_id": history.session_key(r),
            "turn": r.turn,
        }
        for r in history.list_agent_runs(ws, uid)
    ]


@router.delete("/runs/{run_id}")
def delete_run(
    run_id: str, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> dict:
    """删除一个任务会话（含所有追问轮次）。参数可以是会话 id 或任一轮次的 run id。"""
    history = get_history_store()
    runs = history.list_session_runs(run_id, ws, uid)
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    deleted = history.delete_session(run_id, ws, uid)
    return {"ok": True, "deleted": deleted}


class ApproveRequest(BaseModel):
    session_id: str
    tool: str = ""
    # 参数指纹：批准只对「这一组参数」有效，参数变了要重新确认
    args_hash: str = ""
    decision: str = "approve"   # approve | reject
    # once = 只批这一次；session = 「本次会话都允许」，后续同类调用不再询问
    scope: str = "once"
    message: str = ""           # reject 时给模型的指示


@router.post("/approve")
def approve_tool(
    payload: ApproveRequest,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """对一次待确认的敏感工具调用作出决定。

    记录按 (workspace, owner, session) 落库，因此：别人的 session 查不到任何
    待确认记录（统一 404，不区分"不存在"与"不是你的"），批准带 TTL，
    过期即失效。前端拿到 200 后发 `/agent/run`（resume=true）从断点续跑。
    """
    session_id = payload.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id 必填")
    row = approvals.decide(
        ws, uid, session_id,
        decision=payload.decision, args_hash=payload.args_hash.strip(),
        scope=payload.scope if payload.scope in ("once", "session") else "once",
    )
    if row is None:
        raise HTTPException(status_code=404, detail="没有待确认的操作")
    return {
        "ok": True,
        "session_id": session_id,
        "tool": row["tool"],
        "decision": row["decision"],
        "scope": payload.scope,
    }


def _resolve_mounts(
    ws: str, uid: str, req: AgentRunRequest,
) -> tuple[str, list[str], float | None, list[str]]:
    """解析人设与技能挂载，返回 (persona 段, pinned 技能名, temperature, 专家 kb_ids)。

    规则：显式请求参数覆盖专家默认；技能集合 = 去重(req ∪ expert.skill_ids)；
    无效/跨工作区技能静默丢弃（与 kb_ids 同一防探测原则）。
    专家挂载的知识库随专家带出，与请求 kb_ids 合并——chat 链路（chat.py）
    同样语义：专家挂的 KB 在两条链路里都必须生效。
    """
    from app.services import skill_publisher
    from app.services.experts import get_expert_store

    persona = ""
    temperature: float | None = None
    default_skill_ids: list[str] = []
    expert_kb_ids: list[str] = []
    model_id = ""
    if req.expert_id:
        expert = get_expert_store().visible(req.expert_id, ws, uid)
        if expert and expert.enabled:
            if expert.persona.strip():
                persona = f"【人设】{expert.persona.strip()}"
            temperature = expert.temperature
            model_id = getattr(expert, "model_id", "") or ""
            default_skill_ids = list(expert.skill_ids)
            expert_kb_ids = list(expert.kb_ids)

    # 点名技能与专家挂载技能都要收敛到「调用者可见」：别人的自定义技能
    # 不能靠 id 挂进来（预置 '' 与自己创建的才通过）
    from app.services.skills import get_skill_store

    skill_store = get_skill_store()
    skill_ids = [
        sid for sid in dict.fromkeys([*req.skill_ids, *default_skill_ids])
        if skill_store.visible(sid, ws, uid)
    ]

    # PPT 类任务自动钉装 slidep 版式技能：本地模型极少主动渐进披露去读
    # SKILL.md，不钉装则 DSL 语法与设计规范根本不进上下文——质量与
    # 「不用脚本手搓」的合规都塌。钉装 = SKILL.md 全文强制装载。
    task_text = req.task or ""
    if any(k in task_text for k in ("PPT", "ppt", "幻灯", "演示文稿", "slide", "deck", "Slides")):
        for sk in skill_store.list_skills(ws, uid):
            if sk.enabled and sk.name == "slidep 演示文稿设计" and sk.id not in skill_ids:
                skill_ids.append(sk.id)
                break

    pinned = skill_publisher.pinned_slugs(ws, skill_ids)
    return persona, pinned, temperature, expert_kb_ids, model_id


async def _event_stream(req: AgentRunRequest, ws: str, uid: str) -> AsyncIterator[str]:
    history = get_history_store()
    # 会话：追问沿用同一会话，新任务分配新 id
    session_id = req.session_id or uuid.uuid4().hex[:12]
    prior_runs = history.list_session_runs(session_id, ws, uid) if req.session_id else []
    turn = len(prior_runs) + 1
    # 把最近几轮问答带给模型（每轮答案截断，防止本地模型上下文被撑爆）
    lc_history: list[dict] = []
    for r in prior_runs[-3:]:
        lc_history.append({"role": "user", "content": r.task})
        if r.answer:
            lc_history.append({"role": "assistant", "content": r.answer[:1500]})
    try:
        # 只允许检索当前工作区的知识库；项目名给定时必须已存在
        (
            persona, pinned_skills, temperature, expert_kb_ids, expert_model_id,
        ) = _resolve_mounts(ws, uid, req)
        # 模型优先级：本轮显式指定 > 专家偏好 > 平台默认 > .env
        effective_model_id = req.model_id or expert_model_id
        kb_ids = get_store().owned_kb_ids(ws, [*req.kb_ids, *expert_kb_ids], uid)
        ctx = AgentContext(
            workspace_id=ws, owner_id=uid, project=req.project,
            model_id=effective_model_id,
            # 续跑那一次装配必须保留中断节点，否则检查点里待确认的调用永远解不开。
            # 「本会话都允许」的豁免只对新发起的运行生效。
            resuming=bool(req.resume),
        )
        # 兜底路径用：检查点丢了（服务重启）时按已批准的工具重跑，不再弹框
        ctx.approved_tools = approvals.granted_tools(ws, uid, session_id)
        if ctx.project:
            # 挂载的项目必须属于当前用户：别人的项目名一律当「不存在」
            try:
                projects.require(ws, ctx.project, uid)
            except sandbox.SandboxError as exc:
                yield _sse("error", {"message": str(exc)})
                return
    except Exception as exc:  # noqa: BLE001 — 建流阶段失败也要回 error 事件，不能静默断流
        logger.error("agent 流启动失败：%s: %s", type(exc).__name__, exc)
        yield _sse("error", {"message": f"{type(exc).__name__}: {exc}"})
        return

    # 人工确认的恢复：批准/拒绝之后不重发任务，而是带着同一个 thread 续跑。
    # 检查点不存在时引擎会退回「按批准重跑」并在事件流里说明。
    thread_id = f"{ws}:{session_id}"
    resume_decision: dict | None = None
    if req.resume:
        record = approvals.latest_decision(ws, uid, session_id)
        if record is None:
            yield _sse("error", {
                "code": "approval_expired",
                "message": "这次确认已过期或不存在（有效期 30 分钟），请重新发起任务。",
            })
            return
        if record["decision"] == "reject":
            resume_decision = {
                "type": "reject",
                "message": req.approval_message or "用户拒绝了这个操作，请改用不需要它的做法，或先说明为什么必须用它。",
            }
        else:
            resume_decision = {"type": "approve"}
        # 一次确认只用一次：续跑开始就消费，避免同一条记录被反复复用
        approvals.consume(record["id"])
        logger.info("恢复被中断的运行 session=%s decision=%s", session_id, record["decision"])

    if pinned_skills:
        from app.services import audit as audit_svc

        audit_svc.record(
            workspace_id=ws, key_id=None, key_name=None,
            method="AGENT_SKILLS", path="/agent/run", status=200,
            duration_ms=0.0, detail=",".join(pinned_skills),
        )
        logger.info("本轮固定技能：%s", ",".join(pinned_skills))

    # 执行与 SSE 解耦：任务跑在独立 task 里，事件先写登记簿，这里只做转发。
    # 客户端断开（切标签页/网络闪断）不再连带把任务掐掉，产物与运行记录都还在。
    run = run_registry.create(session_id=session_id, workspace_id=ws, owner_id=uid)
    yield _sse("meta", {"run_id": run.run_id, "session_id": session_id, "turn": turn})
    logger.info(
        "%s 开始 session=%s turn=%s task=%r", run.run_id, session_id, turn, req.task[:40]
    )

    async def _execute() -> None:
        """真正干活的那一半：跑引擎、翻译事件、落库。与是否有人看无关。"""
        final_text = ""
        citations: list[dict] = []
        steps: list[dict] = []
        artifacts: list[dict] = []
        answer_started = False
        streamed = ""
        status = "ok"
        error_kind = ""
        interrupt_tool = ""

        _settings = get_settings()
        traces.start(
            kind="agent", workspace_id=ws, owner_id=uid, session_id=session_id,
            engine=_settings.agent_engine,
            # 记实际生效的模型，成本看板按它聚合
            model=models.resolve(effective_model_id, workspace_id=ws).model,
        )

        try:
            async for step in run_agent(
                req.task, req.tool_ids, kb_ids, ws, ctx, history=lc_history,
                persona=persona, pinned_skills=pinned_skills, temperature=temperature,
                thread_id=thread_id, resume=resume_decision,
            ):
                kind = step["kind"]
                if kind == "final":
                    final_text = step["text"]
                    citations = step.get("citations") or []
                    streamed = step.get("streamed") or ""
                elif kind == "delta":
                    # 真流式：第一个增量到达就开答，前端不用等全文
                    if not answer_started:
                        answer_started = True
                        run.push("answer_start", {})
                    run.push("delta", {"text": step["text"]})
                elif kind == "answer_reset":
                    # 上一轮的"过程中的话"作废（模型开始调工具了）
                    run.push("answer_reset", {})
                    answer_started = False
                elif kind == "approval_required":
                    # 记下「到底在批什么」：/approve 凭这条记录定位并要求归属匹配
                    interrupt_tool = step.get("tool", "")
                    approvals.request(
                        ws, uid, session_id, step.get("tool", ""),
                        args_hash=step.get("args_hash", ""),
                    )
                    steps.append(step)
                    run.push("step", {"step": step})
                elif kind == "artifact":
                    entry = {"path": step.get("path", ""), "kind": step.get("artifact_kind", "file")}
                    if entry not in artifacts:
                        artifacts.append(entry)
                        run.push("artifact", {"artifact": entry})
                else:
                    steps.append(step)
                    if kind in ("tool_result", "tool"):
                        traces.note_tool(
                            step.get("tool") or step.get("name") or "tool",
                            int(step.get("duration_ms") or step.get("ms") or 0),
                            status="error" if step.get("error") else "ok",
                            detail=str(step.get("output") or step.get("detail") or "")[:200],
                        )
                    run.push("step", {"step": step})

            if citations:
                run.push("citations", {"citations": citations})

            # 补发尾部：真流式已经把大部分正文发过了，只补没发到的部分；
            # 模型改了稿（与已流出内容不一致）或压根没流式，就整段重发。
            tail = final_text
            if streamed and final_text.startswith(streamed):
                tail = final_text[len(streamed):]
            elif streamed:
                run.push("answer_reset", {})
                answer_started = False
            if tail:
                if not answer_started:
                    answer_started = True
                    run.push("answer_start", {})
                for piece in chunk_text(tail):
                    run.push("delta", {"text": piece})
            elif not answer_started:
                run.push("answer_start", {})

            run.push("done", {
                "run_id": run.run_id,
                "session_id": session_id,
                "project": ctx.project,
                "artifacts": artifacts,
            })
            logger.info("%s 完成 steps=%d", run.run_id, len(steps))

            # 持久化运行历史（同一会话的多轮）
            history.add_agent_run(
                task=req.task,
                tool_ids=req.tool_ids,
                steps=steps,
                answer=final_text,
                status=status,
                workspace_id=ws,
                project=ctx.project,
                artifacts=artifacts,
                session_id=session_id,
                turn=turn,
                owner_id=uid,
            )
        except asyncio.CancelledError:
            # 两种取消要分开讲：用户点了「停止」是预期行为，服务关闭才是事故。
            # 以前统一报「服务正在关闭」，用户点了停止会以为服务挂了。
            stopped = run.cancel_reason == "user"
            status = "stopped" if stopped else "failed"
            try:
                history.add_agent_run(
                    task=req.task, tool_ids=req.tool_ids, steps=steps, answer=final_text,
                    status=status, workspace_id=ws, project=ctx.project,
                    artifacts=artifacts, session_id=session_id, turn=turn, owner_id=uid,
                )
            except Exception:  # noqa: BLE001
                pass
            if stopped:
                # 已经写下的产物照旧交给前端：停止不等于丢弃成果
                run.push("step", {"step": {
                    "kind": "note",
                    "content": "已按你的要求停止本次运行，已完成的步骤与产物保留在项目里。",
                }})
                run.push("done", {
                    "run_id": run.run_id,
                    "session_id": session_id,
                    "project": ctx.project,
                    "artifacts": artifacts,
                })
            else:
                run.push("error", {"message": "服务正在关闭，本次运行被中断。"})
            raise
        except Exception as exc:  # noqa: BLE001 — 统一转 SSE error 事件
            status = "failed"
            error_kind = type(exc).__name__
            logger.exception("%s 运行失败：%s: %s", run.run_id, type(exc).__name__, exc)
            # 失败也落一条历史：产物可能已经写到磁盘上，不给记录等于"这次根本没跑过"
            try:
                history.add_agent_run(
                    task=req.task, tool_ids=req.tool_ids, steps=steps, answer=final_text,
                    status="failed", workspace_id=ws, project=ctx.project,
                    artifacts=artifacts, session_id=session_id, turn=turn, owner_id=uid,
                )
            except Exception as save_exc:  # noqa: BLE001 — 落库失败不能盖掉原始错误
                logger.warning("%s 失败记录未能落库：%s", run.run_id, save_exc)
            run.push("error", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            try:
                traces.finish(
                    status="ok" if status == "ok" else "error",
                    error_kind=error_kind,
                    interrupt=interrupt_tool,
                )
            except Exception as exc:  # noqa: BLE001 — 埋点失败不能影响运行结果
                logger.warning("trace 写入失败：%s: %s", type(exc).__name__, exc)
            run.finish(failed=status != "ok")

    task = asyncio.create_task(_execute())
    run_registry.attach_task(run.run_id, task)

    async for chunk in _forward(run):
        yield chunk


async def _forward(run: run_registry.RunRecord) -> AsyncIterator[str]:
    """把登记簿里的事件转成 SSE。断了就退出，运行本身不受影响。

    同一个函数既服务「首次连接」也服务「刷新后重连接回」：登记簿是只增日志，
    从第 0 条重放即可，前端因此不会看到半个答案。
    """
    index = 0
    while True:
        while index < len(run.events):
            event, data = run.events[index]
            index += 1
            yield _sse(event, data)
        if run.done:
            break
        # clear 与 await 之间可能来新事件：先清再复查长度，避免漏掉唤醒
        run.changed.clear()
        if index < len(run.events):
            continue
        try:
            await asyncio.wait_for(run.changed.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            # 大模型思考期间流上不能长时间无数据，否则中间层会掐断连接
            yield ": ping\n\n"


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> dict:
    """停止一次正在运行的智能体任务。

    前端以前的「停止」只是 abort 掉 SSE 连接——执行在后台 task 里照跑，
    模型继续烧 token、继续写文件。这里才是真正的取消：撤销 asyncio task，
    运行方按 `cancel_reason` 把状态记成 stopped 并把已产出的文件交给前端。

    别人的 run_id 一律 404（与资源隔离的其它接口同一语义）；任务在别的
    副本上时回 409 —— 假称停掉了比停不掉更糟。
    """
    state = run_registry.cancel(run_id, workspace_id=ws, owner_id=uid)
    if state == "missing":
        raise HTTPException(status_code=404, detail="运行不存在或已过期")
    if state == "remote":
        raise HTTPException(
            status_code=409,
            detail="这次运行在另一个服务实例上，当前实例停不了它。已保存的进度不会丢，等它跑完或刷新页面重新连上后再试。",
        )
    return {"ok": True, "state": state}

@router.get("/runs/active")
def list_active_runs(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> list[dict]:
    """当前空间里还没结束的运行。前端刷新后靠它重新挂上 SSE 订阅。

    内存登记簿与 `run_events` 里未收尾的运行取并集：前者是本进程刚创建的
    （最新），后者补上「在别的副本上创建、本进程没见过」的那些。多副本
    部署时靠这一条才不会刷新即丢。
    """
    return run_registry.active(ws, uid)


@router.get("/runs/{run_id}/stream")
def reconnect_run(
    run_id: str,
    ws: str = Depends(current_workspace),
    uid: str = Depends(current_uid),
) -> StreamingResponse:
    """重连接回一次运行：从已有事件重放，然后继续跟到结束。

    用于「刷新页面/切回标签页」：任务一直在后台跑，这里只是重新挂上一个读者。
    别人的 run_id 一律 404（与资源隔离的其它接口同一语义）。

    两条路径：本进程创建过这次运行，直接读内存（无延迟、带唤醒信号）；
    否则回落到 `run_events` 表重放 + 短轮询——这正是「刷新后被打到另一个
    副本」的情况，任务在别的进程里好好跑着，不该给用户一个 404。
    """
    run = run_registry.get(run_id, workspace_id=ws, owner_id=uid)
    if run is not None:
        return StreamingResponse(
            _forward(run),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    if not run_registry.has_persisted(run_id, workspace_id=ws, owner_id=uid):
        raise HTTPException(status_code=404, detail="运行不存在或已过期")
    return StreamingResponse(
        _forward_persisted(run_id, ws=ws, uid=uid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _forward_persisted(run_id: str, *, ws: str, uid: str) -> AsyncIterator[str]:
    """跨进程跟读：从库里重放，再轮询新事件。

    事件序与内存路径完全一致（都是 seq 升序），所以前端不需要区分这两条
    路径——重置状态、从头接收即可。
    """
    async for event, data in run_registry.follow_persisted(
        run_id, workspace_id=ws, owner_id=uid,
    ):
        yield _sse(event, data)


@router.post("/run")
def run(
    req: AgentRunRequest, ws: str = Depends(current_workspace), uid: str = Depends(current_uid),
) -> StreamingResponse:
    """SSE 运行智能体。事件：meta -> (step|artifact)* -> [citations] -> answer_start -> delta* -> done | error。"""
    # Token 日配额：以前只累加不拦截，`APP_QUOTA_DAILY_TOKENS` 形同虚设
    allowed, used = ratelimit.quota_check(ws, "tokens")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"今日 Token 配额已用完（已用 {used}）。配额按自然日重置，或联系管理员调整。",
        )
    return StreamingResponse(
        _event_stream(req, ws, uid),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
