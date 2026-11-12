"""HTML 幻灯片套件：CSS 排版 → 浏览器实测几何 → 原生 pptx 形状。

为什么要有这一层（替代 design_renderer 的手写英寸坐标）：
- 手写坐标没有布局引擎，文字行数靠 `_estimate_lines` 猜，猜错就溢出或留白；
- 这里把「排版」交给 Chromium：Flexbox/Grid 真实计算，逐行矩形实测；
- 再把实测矩形写成 pptx 的矩形/文本框，输出仍是**原生可编辑形状**，
  DeckCraft 打开后每个字都能改，不是导出图片。

模块分工：
- tokens      设计令牌（配色家族 / 字阶 / 间距 / 图表色带）
- stylesheet  锁定的 CSS 设计系统（组件类名就是给合成器用的词汇表）
- compose     确认卡 → 每页 HTML（确定性版式编排，也是预览与兜底路径）
- measure     Node + playwright-core：渲染 HTML，抽几何与计算样式，出缩略图
- lint        溢出/裁切/重叠/对比度/填充率检查 + 确定性修复
- pptx_writer 实测几何 → python-pptx 原生形状
- renderer    编排上述步骤，接口与 DesignRenderer.render 对齐
"""

from app.services.ppt_studio.htmlkit.tokens import CANVAS_H, CANVAS_W, resolve_tokens

__all__ = ["CANVAS_W", "CANVAS_H", "resolve_tokens"]