"""告警规则与事件。

管理面在 `/api/alerts`：建规则、改阈值、试发一条、看历史。真正的评估由
`POST /alerts/check`（手动）或后台定时任务（每 `APP_ALERT_INTERVAL_MINUTES`
分钟）触发，两者调同一个函数，页面上"现在到底会不会报"和定时器结论一致。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import app.services.alerts as alerts
from app.api.deps import current_uid, current_user, current_workspace
from app.services import users

router = APIRouter(prefix="/alerts", tags=["alerts"])


class Channel(BaseModel):
    type: str = "webhook"
    url: str = ""


class RuleCreate(BaseModel):
    name: str
    kind: str = "error_rate"
    threshold: float = Field(ge=0)
    window_hours: int = 24
    channels: list[Channel] = Field(default_factory=list)
    cooldown_minutes: int = 60
    enabled: bool = True


class RulePatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    threshold: float | None = Field(default=None, ge=0)
    window_hours: int | None = None
    channels: list[Channel] | None = None
    cooldown_minutes: int | None = None
    enabled: bool | None = None


@router.get("/kinds")
def list_kinds() -> list[dict]:
    """可选的告警类型与默认阈值建议，前端下拉直接用。"""
    return [
        {"kind": "error_rate", "label": "错误率", "unit": "比例", "suggest": 0.2},
        {"kind": "miss_rate", "label": "未命中率", "unit": "比例", "suggest": 0.4},
        {"kind": "cost_daily", "label": "每日花费", "unit": "元", "suggest": 50},
        {"kind": "model_down", "label": "不可用模型数", "unit": "个", "suggest": 1},
    ]


@router.get("/rules")
def list_rules(
    ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> list[dict]:
    return [alerts.rule_payload(r) for r in alerts.list_rules(ws, uid)]


@router.post("/rules")
def create_rule(
    req: RuleCreate, ws: str = Depends(current_workspace), uid: str = Depends(current_uid)
) -> dict:
    try:
        rule = alerts.create_rule(
            workspace_id=ws, owner_id=uid, name=req.name, kind=req.kind,
            threshold=req.threshold, window_hours=req.window_hours,
            channels=[c.model_dump() for c in req.channels],
            cooldown_minutes=req.cooldown_minutes, enabled=req.enabled,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return alerts.rule_payload(rule)


def _require_edit(rule_id: str, ws: str, uid: str, is_admin: bool) -> dict:
    rule = alerts.get_rule(rule_id)
    if rule is None or rule.get("workspace_id") != ws:
        raise HTTPException(404, "告警规则不存在")
    owner = rule.get("owner_id", "")
    if owner != uid and not (owner == "" and is_admin):
        raise HTTPException(403, "只有创建者或管理员可以修改这条规则")
    return rule


@router.patch("/rules/{rule_id}")
def update_rule(
    rule_id: str, req: RulePatch,
    ws: str = Depends(current_workspace), user: users.User = Depends(current_user),
) -> dict:
    _require_edit(rule_id, ws, user.id, user.is_admin)
    patch = req.model_dump(exclude_none=True)
    if "channels" in patch:
        patch["channels"] = [c for c in patch["channels"] if c.get("url")]
    try:
        rule = alerts.update_rule(rule_id, patch)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rule is None:
        raise HTTPException(404, "告警规则不存在")
    return alerts.rule_payload(rule)


@router.delete("/rules/{rule_id}")
def delete_rule(
    rule_id: str, ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    _require_edit(rule_id, ws, user.id, user.is_admin)
    return {"ok": alerts.delete_rule(rule_id)}


@router.post("/rules/{rule_id}/test")
async def test_rule(
    rule_id: str, ws: str = Depends(current_workspace),
    user: users.User = Depends(current_user),
) -> dict:
    """发一条测试消息。配错 webhook 地址是最常见的故障，先试一下再上线。"""
    rule = _require_edit(rule_id, ws, user.id, user.is_admin)
    channels = rule.get("channels") or []
    if not channels:
        raise HTTPException(400, "这条规则还没配通知渠道")
    text = f"【Einstein 测试】{rule.get('name')} 的通知渠道已连通。"
    ok, error = await alerts.deliver(channels, text)
    return {"ok": ok, "error": error}


@router.get("/events")
def list_events(
    limit: int = Query(50, le=500), ws: str = Depends(current_workspace)
) -> list[dict]:
    return alerts.list_events(ws, limit)


@router.post("/check")
async def check_now(ws: str = Depends(current_workspace)) -> dict:
    """立即评估一遍当前空间的规则（页面上的"立即检查"按钮）。"""
    return await alerts.evaluate(ws)
