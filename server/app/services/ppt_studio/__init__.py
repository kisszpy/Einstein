"""AI PPT 制作流水线：模板解析 → 取材出卡 → 套母版生成。

对外只暴露四个入口，路由层与测试都只碰这几个名字：
- `parse_theme_pack`：.pptx 字节 → 主题包（色板/字体/版式/保真度）
- `build_outline`：确认卡数据模型的工厂（校验 + 规范化都在这里）
- `analyze`：取材 + 出卡的 SSE 事件流
- `generate_deck`：校验 → 套母版渲染 → 落盘 deck/ → 签票 → 结果卡载荷
"""

from app.services.ppt_studio.outline import (
    Outline,
    OutlinePage,
    OutlineSection,
    OutlineValidationError,
    SourceRef,
    StyleSpec,
)
from app.services.ppt_studio.theme_pack import parse_theme_pack

__all__ = [
    "Outline",
    "OutlinePage",
    "OutlineSection",
    "OutlineValidationError",
    "SourceRef",
    "StyleSpec",
    "build_outline",
    "parse_theme_pack",
]


def build_outline(data: dict) -> Outline:
    """从任意 dict（模型输出 / 前端回传）构造一份规范化的大纲。

    校验失败的报错统一走 `OutlineValidationError`，路由层转 400。
    """
    outline = Outline.model_validate(data or {}).normalize()
    outline.validate()
    return outline
