"""告警：规则、评估、投递。

没有告警的运维等于事后考古——值班的人只能等用户来投诉。这里不做通用规则
引擎（阈值表达式 DSL 那种），只覆盖自托管部署最常出事的四类：

- `error_rate`  错误率超过阈值（模型挂了、端点限流、工具崩溃）
- `miss_rate`   未命中率超过阈值（知识库覆盖不住真实提问，是该补资料还是该调门限）
- `cost_daily`  单日花费超过阈值（智能体跑飞了，一夜之间烧掉一个月预算）
- `model_down`  注册表里有模型连通性检测失败（端点迁移、密钥过期）

三条设计取舍：

1. **冷却而不是去重**：同一规则在 `cooldown_minutes` 内只报一次。持续故障
   每 10 分钟提醒一次，等于让所有人屏蔽这个群。
2. **事件不可变**：报出去的 `value`/`threshold` 落库存档，改规则不回改历史。
3. **投递失败要留痕**：`delivered=false` + `delivery_error`。告警没送出去
   比没配告警更危险——你以为有人看着。
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings
from app.db.engine import get_db
from app.db.rows import TableState
from app.db.schema import migrate
from app.db.tables import ALERT_EVENTS, ALERT_RULES
from app.services.state import JsonState

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_JSON_RULES = JsonState(_DATA_DIR / "alert_rules.json")
_JSON_EVENTS = JsonState(_DATA_DIR / "alert_events.json")
_lock = threading.Lock()

KINDS = ("error_rate", "miss_rate", "cost_daily", "model_down")
KIND_LABEL = {
    "error_rate": "错误率",
    "miss_rate": "未命中率",
    "cost_daily": "每日花费（元）",
    "model_down": "不可用模型数",
}
MAX_EVENTS = 2000
WEBHOOK_TIMEOUT = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_table() -> bool:
    return get_settings().store_backend != "json"


def _table(name: str) -> TableState:
    db = get_db()
    migrate(db)
    from app.db.tables import TABLES

    return TableState(TABLES[name], db)


# ---------------------------------------------------------------- 规则


def list_rules(workspace_id: str, owner_id: str | None = "") -> list[dict]:
    """列出规则。

    `owner_id=None` 表示"不过滤归属"——只给系统侧（定时评估）用。界面上必须
    传具体用户：告警规则里带着 webhook 地址，别人的不该被看见，更不该被改。
    """
    if _use_table():
        rows = _table("alert_rules").rows("workspace_id = ?", (workspace_id,))
    else:
        rows = [r for r in (_JSON_RULES.read() or []) if r.get("workspace_id") == workspace_id]
    if owner_id is None:
        return rows
    # 预置规则（owner_id=''）与自己的规则都可见；别人的不显示
    return [r for r in rows if r.get("owner_id", "") in ("", owner_id)]


def get_rule(rule_id: str) -> dict | None:
    if _use_table():
        rows = _table("alert_rules").rows("id = ?", (rule_id,), limit=1)
        return rows[0] if rows else None
    return next((r for r in (_JSON_RULES.read() or []) if r.get("id") == rule_id), None)


def create_rule(
    *, workspace_id: str, owner_id: str, name: str, kind: str,
    threshold: float, window_hours: int = 24, channels: list[dict] | None = None,
    cooldown_minutes: int = 60, enabled: bool = True,
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind 只支持 {'/'.join(KINDS)}")
    if not (name or "").strip():
        raise ValueError("规则名称不能为空")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "workspace_id": workspace_id,
        "owner_id": owner_id,
        "name": name.strip(),
        "kind": kind,
        "threshold": float(threshold),
        "window_hours": max(1, min(int(window_hours or 24), 24 * 30)),
        "channels": [c for c in (channels or []) if (c or {}).get("url")],
        "cooldown_minutes": max(0, int(cooldown_minutes or 0)),
        "enabled": bool(enabled),
        "last_fired_at": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        if _use_table():
            _table("alert_rules").upsert(entry)
        else:
            rows = list(_JSON_RULES.read() or [])
            rows.append(entry)
            _JSON_RULES.write(rows)
    return entry


_UPDATABLE = {
    "name", "kind", "threshold", "window_hours", "channels",
    "cooldown_minutes", "enabled",
}


def update_rule(rule_id: str, patch: dict) -> dict | None:
    rule = get_rule(rule_id)
    if rule is None:
        return None
    for key, value in patch.items():
        if key in _UPDATABLE and value is not None:
            rule[key] = value
    if rule.get("kind") not in KINDS:
        raise ValueError(f"kind 只支持 {'/'.join(KINDS)}")
    rule["updated_at"] = _now()
    with _lock:
        if _use_table():
            _table("alert_rules").upsert(rule)
        else:
            rows = [rule if r.get("id") == rule_id else r for r in (_JSON_RULES.read() or [])]
            _JSON_RULES.write(rows)
    return rule


def mark_fired(rule_id: str, when: str) -> None:
    """记下"刚报过"。

    走独立函数而不是 `update_rule`：`update_rule` 的字段白名单是**用户能改的
    东西**，冷却时间戳不是——混进去意味着任何能调 PATCH 的人都能把冷却清零，
    等于关掉防刷屏。系统侧写自己的账，只走这里。
    """
    rule = get_rule(rule_id)
    if rule is None:
        return
    rule["last_fired_at"] = when
    with _lock:
        if _use_table():
            _table("alert_rules").upsert(rule)
        else:
            rows = [rule if r.get("id") == rule_id else r for r in (_JSON_RULES.read() or [])]
            _JSON_RULES.write(rows)


def delete_rule(rule_id: str) -> bool:
    with _lock:
        if _use_table():
            changed = _table("alert_rules").delete_where("id = ?", (rule_id,))
            return bool(changed)
        rows = list(_JSON_RULES.read() or [])
        kept = [r for r in rows if r.get("id") != rule_id]
        if len(kept) == len(rows):
            return False
        _JSON_RULES.write(kept)
        return True


# ---------------------------------------------------------------- 事件


def list_events(workspace_id: str, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    if _use_table():
        return _table("alert_events").rows(
            "workspace_id = ?", (workspace_id,), limit=limit, order="fired_at DESC"
        )
    rows = [r for r in (_JSON_EVENTS.read() or []) if r.get("workspace_id") == workspace_id]
    return sorted(rows, key=lambda r: r.get("fired_at", ""), reverse=True)[:limit]


def _record(event: dict) -> None:
    if _use_table():
        _table("alert_events").upsert(event)
        return
    rows = list(_JSON_EVENTS.read() or [])
    rows.append(event)
    _JSON_EVENTS.write(rows[-MAX_EVENTS:])


# ---------------------------------------------------------------- 指标


def measure(kind: str, workspace_id: str, window_hours: int) -> tuple[float, str]:
    """返回 (当前值, 人话说明)。取值口径与看板保持一致，避免"看板和告警对不上"。"""
    from app.services import models as model_registry
    from app.services import traces

    days = max(1, round(window_hours / 24))

    if kind == "model_down":
        rows = [
            m for m in model_registry.all_models()
            if m.get("workspace_id") in (workspace_id, "default")
            and m.get("last_status") == "error"
        ]
        names = "、".join(m.get("name", "") for m in rows[:5]) or "无"
        return float(len(rows)), f"{len(rows)} 个模型检测失败：{names}"

    summary = traces.usage_summary(workspace_id=workspace_id, days=days)

    if kind == "error_rate":
        runs = summary["totals"]["runs"]
        errors = summary["totals"]["errors"]
        rate = (errors / runs) if runs else 0.0
        return round(rate, 4), f"近 {days} 天 {runs} 次运行，{errors} 次失败（{rate:.1%}）"

    if kind == "miss_rate":
        # 未命中率不在 usage_summary 的口径里，单独从 summary 取
        data = traces.summary(workspace_id=workspace_id, limit=2000)
        return float(data.get("miss_rate", 0)), (
            f"近 {data.get('runs', 0)} 次运行，未命中率 {data.get('miss_rate', 0):.1%}"
        )

    if kind == "cost_daily":
        total = float(summary["totals"]["cost"])
        per_day = total / days if days else 0.0
        return round(per_day, 4), f"近 {days} 天共 {total:.2f} 元，日均 {per_day:.2f} 元"

    return 0.0, ""


# ---------------------------------------------------------------- 投递


def _payload_for(url: str, text: str) -> bytes:
    """按目标域名挑格式：企业微信和钉钉不认裸 JSON 的 text 字段。"""
    host = urllib.parse.urlparse(url).netloc
    if "qyapi.weixin.qq.com" in host:
        body = {"msgtype": "text", "text": {"content": text}}
    elif "oapi.dingtalk.com" in host:
        body = {"msgtype": "text", "text": {"content": text}}
    elif "feishu" in host or "larksuite" in host:
        body = {"msg_type": "text", "content": {"text": text}}
    else:
        body = {"text": text, "source": "einstein"}
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _post(url: str, text: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        url,
        data=_payload_for(url, text),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=WEBHOOK_TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return True, ""
            return False, f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001 — 投递失败如实记录
        return False, f"{type(exc).__name__}: {exc}"


async def deliver(channels: list[dict], text: str) -> tuple[bool, str]:
    """逐个渠道投递，任一成功即算送达；全部失败时把最后一个错误带出来。"""
    if not channels:
        return False, "未配置通知渠道"
    errors: list[str] = []
    for channel in channels:
        url = str(channel.get("url") or "")
        if not url.startswith(("http://", "https://")):
            errors.append(f"{url or '(空)'} 不是合法地址")
            continue
        ok, err = await asyncio.to_thread(_post, url, text)
        if ok:
            return True, ""
        errors.append(err)
    return False, "；".join(errors)[:400]


# ---------------------------------------------------------------- 评估


def _cooled(rule: dict, now: datetime) -> bool:
    stamp = rule.get("last_fired_at") or ""
    if not stamp:
        return True
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return now - when >= timedelta(minutes=int(rule.get("cooldown_minutes") or 0))


async def evaluate(workspace_id: str, *, owner_id: str | None = None) -> dict:
    """跑一遍该空间的所有规则，返回本次触发了哪些。

    设计成"调用即评估"而不是常驻定时器为主：定时器每 N 分钟调一次，
    页面上的"立即检查"也调同一个函数——两条路径一套逻辑，结论不会打架。
    """
    now = datetime.now(timezone.utc)
    fired: list[dict] = []
    checked = 0
    # 系统评估不看归属：一条规则有没有触发，与"谁点的检查"无关
    for rule in list_rules(workspace_id, owner_id):
        if not rule.get("enabled", True):
            continue
        checked += 1
        try:
            value, detail = measure(
                rule.get("kind", ""), workspace_id, int(rule.get("window_hours") or 24)
            )
        except Exception as exc:  # noqa: BLE001 — 单条规则算错不影响其它
            fired.append({"rule_id": rule.get("id"), "error": f"{type(exc).__name__}: {exc}"})
            continue

        # 阈值 <= 0 视为未配置：不报警，避免"什么都没设却天天响"
        threshold = float(rule.get("threshold") or 0)
        if threshold <= 0 or value < threshold:
            continue
        # 冷却期内不重复报：持续故障每 10 分钟响一次，等于让人屏蔽这个群
        if not _cooled(rule, now):
            continue

        label = KIND_LABEL.get(rule.get("kind", ""), rule.get("kind", ""))
        text = (
            f"【Einstein 告警】{rule.get('name')}\n"
            f"{label}：{value}（阈值 {rule.get('threshold')}）\n"
            f"{detail}\n空间：{workspace_id}"
        )
        delivered, error = await deliver(rule.get("channels") or [], text)
        event = {
            "id": uuid.uuid4().hex[:12],
            "workspace_id": workspace_id,
            "rule_id": rule.get("id", ""),
            "rule_name": rule.get("name", ""),
            "kind": rule.get("kind", ""),
            "value": float(value),
            "threshold": float(rule.get("threshold") or 0),
            "message": text,
            "fired_at": _now(),
            "delivered": delivered,
            "delivery_error": error,
        }
        _record(event)
        mark_fired(rule["id"], event["fired_at"])
        fired.append({**event, "detail": detail})

    return {"checked": checked, "fired": fired}


async def evaluate_all() -> dict:
    """定时任务用：遍历所有在规则里出现过的空间。"""
    spaces: set[str] = set()
    try:
        if _use_table():
            spaces = {
                r["workspace_id"] for r in _table("alert_rules").rows()
            }
        else:
            spaces = {r.get("workspace_id", "") for r in (_JSON_RULES.read() or [])}
    except Exception:  # noqa: BLE001 — 表还没建好时静默跳过
        return {"checked": 0, "fired": []}

    total = {"checked": 0, "fired": []}
    for ws in spaces:
        if not ws:
            continue
        try:
            result = await evaluate(ws)
            total["checked"] += result["checked"]
            total["fired"].extend(result["fired"])
        except Exception:  # noqa: BLE001
            continue
    return total


def rule_payload(rule: dict) -> dict:
    return {
        "id": rule.get("id", ""),
        "name": rule.get("name", ""),
        "kind": rule.get("kind", ""),
        "kind_label": KIND_LABEL.get(rule.get("kind", ""), ""),
        "threshold": float(rule.get("threshold") or 0),
        "window_hours": int(rule.get("window_hours") or 24),
        "channels": rule.get("channels") or [],
        "cooldown_minutes": int(rule.get("cooldown_minutes") or 0),
        "enabled": bool(rule.get("enabled", True)),
        "last_fired_at": rule.get("last_fired_at", ""),
        "created_at": rule.get("created_at", ""),
        "owner_id": rule.get("owner_id", ""),
    }
