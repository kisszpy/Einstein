"""内置设计风格（builtin designs）：没有上传公司模板时，也要出「有设计感」的稿子。

为什么单独一层：主题包（theme_pack）解决的是「跟公司模板一致」，它依赖用户上传的
.pptx 当基底；一旦没有模板，旧实现退化成 python-pptx 默认母版（白底 + 默认占位符），
只有配色变了、版式没有任何设计，成稿很难看。

这里定义的是「设计令牌」——调色板 + 字体 + 明暗基调，交给 design_renderer 用形状与
文本框从零绘制版式（封面/章节/内容/两栏/引用/数据/结尾/来源），生成结果仍是普通
pptx 形状，DeckCraft 打开后每个字都能改。

id 统一带 `builtin:` 前缀，与主题包 uuid 不会撞；同时兼容 outline.style.preset_theme
（midnight/graphite/...）的旧口径，两条入口都归一到同一套 preset。
"""

from __future__ import annotations

from typing import Any

FONT_STACK = "Microsoft YaHei"

# 「不指定风格」时按内容生成一套方向，而不是回到某个固定家族。
# 它是风格选择里的一张卡：前端不必改，选了就等于「让系统按内容配」。
AUTO_ID = "builtin:auto"


def wants_auto(theme_pack_id: str, preset_theme: str) -> bool:
    """是否该走按内容生成的方向（而不是某个固定家族或上传模板）。"""
    return theme_pack_id in ("", AUTO_ID) and not (preset_theme or "").strip()

# 每套风格的关键字（tagline 会显示在 UI 风格卡上，别写成营销话术）
_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "key": "midnight",
        "name": "午夜蓝",
        "tagline": "深色封面 · 商务稳重",
        "font": FONT_STACK,
        "palette": {
            "bg": "0B1223",
            "bg_soft": "17223D",
            "paper": "FFFFFF",
            "surface": "F2F5FB",
            "line": "DCE3EF",
            "ink": "101828",
            "ink_muted": "5D6B8A",
            "on_dark": "F7FAFF",
            "on_dark_muted": "9FB0CC",
            "accent": "4F6BF6",
            "accent_soft": "C9D3FB",
            "accent2": "14B8C4",
        },
    },
    {
        "key": "graphite",
        "name": "石墨",
        "tagline": "极简黑白 · 金色点缀",
        "font": FONT_STACK,
        "palette": {
            "bg": "141416",
            "bg_soft": "242428",
            "paper": "FFFFFF",
            "surface": "F5F5F4",
            "line": "E4E4E7",
            "ink": "18181B",
            "ink_muted": "71717A",
            "on_dark": "FAFAFA",
            "on_dark_muted": "A1A1AA",
            "accent": "C2A15A",
            "accent_soft": "EADFC1",
            "accent2": "8A8A93",
        },
    },
    {
        "key": "ocean",
        "name": "深海",
        "tagline": "蓝绿渐变 · 科技感",
        "font": FONT_STACK,
        "palette": {
            "bg": "041F33",
            "bg_soft": "0A2E49",
            "paper": "FFFFFF",
            "surface": "EDF6FB",
            "line": "D3E6F1",
            "ink": "072B42",
            "ink_muted": "4E7A93",
            "on_dark": "F0F9FF",
            "on_dark_muted": "8CC6E8",
            "accent": "0E9BD8",
            "accent_soft": "BFE4F5",
            "accent2": "10B981",
        },
    },
    {
        "key": "sunset",
        "name": "暮色",
        "tagline": "紫调封面 · 品牌发布会",
        "font": FONT_STACK,
        "palette": {
            "bg": "271A35",
            "bg_soft": "3A2749",
            "paper": "FFFFFF",
            "surface": "FBF1F5",
            "line": "EEDCE5",
            "ink": "2B1B32",
            "ink_muted": "7A5C86",
            "on_dark": "FFF7ED",
            "on_dark_muted": "D8B4C0",
            "accent": "E11D63",
            "accent_soft": "F6C7D7",
            "accent2": "E8A03C",
        },
    },
    {
        "key": "forest",
        "name": "松林",
        "tagline": "墨绿 · 稳健汇报",
        "font": FONT_STACK,
        "palette": {
            "bg": "0E241C",
            "bg_soft": "1B3A2D",
            "paper": "FFFFFF",
            "surface": "EEF6F1",
            "line": "D7E8DE",
            "ink": "14261E",
            "ink_muted": "557061",
            "on_dark": "F2FAF5",
            "on_dark_muted": "9CC3AE",
            "accent": "1F9D6B",
            "accent_soft": "BFE6D5",
            "accent2": "D9A441",
        },
    },
    {
        "key": "ivory",
        "name": "象牙",
        "tagline": "浅色纸张 · 学术/研究",
        "font": FONT_STACK,
        "palette": {
            # 唯一一套「浅色封面」：纸张质感，适合研究报告与学术答辩
            "bg": "FAF7F0",
            "bg_soft": "F1EAD9",
            "paper": "FFFDF8",
            "surface": "F4EEE0",
            "line": "E3D9C6",
            "ink": "1F1B16",
            "ink_muted": "6B6257",
            "on_dark": "1F1B16",
            "on_dark_muted": "6B6257",
            "accent": "B4552A",
            "accent_soft": "EAD3C2",
            "accent2": "2F6B5B",
        },
    },
)

DEFAULT_KEY = "midnight"
BUILTIN_PREFIX = "builtin:"

_PRESET_BY_KEY: dict[str, dict[str, Any]] = {p["key"]: p for p in _PRESETS}


def preset_id(key: str) -> str:
    return f"{BUILTIN_PREFIX}{key}"


def default_preset() -> dict[str, Any]:
    return _PRESET_BY_KEY[DEFAULT_KEY]


def all_presets() -> list[dict[str, Any]]:
    return list(_PRESETS)


def is_builtin_id(value: str) -> bool:
    return (value or "").startswith(BUILTIN_PREFIX)


def resolve_id(value: str) -> dict[str, Any] | None:
    """`builtin:ocean` → preset；非内置 id 返回 None（交给 theme_pack 处理）。"""
    if not is_builtin_id(value):
        return None
    key = value[len(BUILTIN_PREFIX):].strip().lower()
    return _PRESET_BY_KEY.get(key)


def resolve_theme(value: str) -> dict[str, Any] | None:
    """兼容 outline.style.preset_theme 的旧键（midnight/ocean/...）。"""
    return _PRESET_BY_KEY.get((value or "").strip().lower())


def resolve(value_id: str = "", theme: str = "") -> dict[str, Any]:
    """按 theme_pack_id 优先、preset_theme 次之解析；都认不出时回落默认。"""
    return resolve_id(value_id) or resolve_theme(theme) or default_preset()


def list_payload() -> list[dict[str, Any]]:
    """GET /ppt/templates 的「内置风格」段：字段与主题包摘要同形，方便前端复用卡片。"""
    auto = {
        "id": AUTO_ID,
        "kind": "builtin",
        "name": "智能方向",
        "tagline": "按内容生成配色与构图，每份稿子不一样",
        "owner_id": "",
        "status": "ready",
        "layout_count": 0,
        "fidelity_score": 100,
        "swatches": ["111214", "6F96B8", "F3F1EC"],
        "created_at": "",
    }
    return [auto] + [
        {
            "id": preset_id(p["key"]),
            "kind": "builtin",
            "name": p["name"],
            "tagline": p["tagline"],
            "owner_id": "",
            "status": "ready",
            "layout_count": 0,
            "fidelity_score": 100,
            "swatches": [
                p["palette"]["bg"],
                p["palette"]["accent"],
                p["palette"]["paper"],
            ],
            "created_at": "",
        }
        for p in _PRESETS
    ]
