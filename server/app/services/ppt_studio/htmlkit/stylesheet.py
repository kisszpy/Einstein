"""锁定的 CSS 设计系统：合成器只准用这里的类名，颜色只准用 tokens 注入的变量。

**硬约束（决定了这套 CSS 能写成什么样）**：每个视觉元素最终要变成 pptx 原生形状，
所以只允许「可映射」的 CSS：
- 可映射：flex 布局、纯色/线性渐变背景、圆角、描边、透明度、绝对定位、
  字号字重字色字距行高。
- 禁止：滤镜、混合模式、mask、conic-gradient、text-shadow、transform、z-index、
  伪元素承载内容（::before 的 content 测不到 DOM 文本）。
  需要装饰就用真实 div，需要环形图就用带 data-pptx 指令的 SVG。

**填充纪律**：内容区一律 flex:1 撑满版心，条目再 flex:1 均分剩余高度。
「三条要点浮在大片空白中间」是上一版渲染器最大的丑源，这里用布局根治，
不靠 lint 事后补救。
"""

from __future__ import annotations


# 组件类名清单：compose 用它自检，将来若接模型自由排版用它做白名单
COMPONENT_CLASSES = (
    "slide", "canvas", "head", "kicker", "h1", "h2", "h3", "lead", "body", "small",
    "rule", "rule--accent", "cols", "col", "card", "card--flat", "card--accent", "card--topline",
    "card-top", "card-metric", "card-num", "card-unit", "card-ghost",
    "chip", "bullets", "b-item", "b-band", "b-no", "b-text", "num-hl",
    "stats", "stat", "stat-num", "stat-unit", "stat-label", "stat-note",
    "metric-spot", "metric-hero", "metric-label", "metric-big", "metric-unit",
    "metric-from", "metric-notes", "metrics", "metric-row", "metric-head",
    "metric-value", "metric-delta", "metric-name", "metric-icon", "metric-track", "metric-fill", "metric-mark",
    "metric-bars", "metric-bar", "metric-bar-fill", "metric-bar-tags", "metric-foot",
    "bars", "bar-row", "bar-label", "bar-track", "bar-fill", "bar-value",
    "columns", "col-item", "col-bar", "col-value", "col-label", "col-base",
    "timeline", "tl-item", "tl-rail", "tl-dot", "tl-line", "tl-band", "tl-time", "tl-text",
    "quote", "quote-rail", "quote-body", "quote-text", "quote-by", "quote-role", "quote-credit",
    "dtable", "dtr", "dth", "dtd",
    "footer", "foot-no", "foot-src", "end-wrap", "end-title", "end-sub",
    "cover-wrap", "cover-title", "cover-sub", "cover-meta", "cover-stats",
    "sec-wrap", "sec-left", "sec-index", "sec-title", "sec-list", "sec-item", "sec-no", "sec-name",
    "m-orb", "m-orb--sm", "m-gridline", "m-corner", "m-bar",
    "appendix", "apx-item", "apx-no", "apx-title", "apx-url",
    "chart-wrap", "chart-head", "chart-legend", "lg-item", "lg-dot", "lg-label",
    "lg-value", "lg-track", "lg-fill", "chart-note", "chart-delta", "donut-wrap", "donut-center",
    "spacer", "grow",
)


def stylesheet() -> str:
    """基础样式表。变量由 tokens.css_vars 注入到 :root，这里不写死任何色值。"""
    return f"""
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ background:#20242C; }}
body {{ font-family: var(--font-body); -webkit-font-smoothing:antialiased; }}

.slide {{
  position:relative; width:var(--canvas-w); height:var(--canvas-h); overflow:hidden;
  background:var(--c-bg); color:var(--c-ink);
  font-family:var(--font-body); font-size:var(--t-body); line-height:1.55;
}}
.slide[data-kind="cover"], .slide[data-kind="section"], .slide[data-kind="end"] {{
  background:var(--cover-bg);
}}
.canvas {{
  position:absolute; left:var(--margin); top:var(--margin);
  right:var(--margin); bottom:var(--margin);
  display:flex; flex-direction:column;
}}
.grow {{ flex:1 1 auto; min-height:0; }}
.spacer {{ flex:0 0 auto; }}

/* ---------------- 文字层级 ---------------- */
.kicker {{
  font-family:var(--font-display); font-size:var(--t-kicker); font-weight:600;
  letter-spacing:.22em; color:var(--c-accent-ink); line-height:1.5;
}}
.h1 {{ font-family:var(--font-display); font-size:var(--t-h1); font-weight:700; line-height:normal; letter-spacing:-.01em; color:var(--c-ink); text-wrap:balance; }}
.h2 {{ font-family:var(--font-display); font-size:var(--t-h2); font-weight:700; line-height:normal; color:var(--c-ink); text-wrap:balance; }}
.h3 {{ font-family:var(--font-display); font-size:var(--t-h3); font-weight:600; line-height:normal; color:var(--hue, var(--c-ink)); text-wrap:balance; }}
.lead {{ font-size:var(--t-lead); font-weight:400; line-height:1.55; color:var(--c-muted); text-wrap:pretty; }}
.body {{ font-size:var(--t-body); font-weight:400; line-height:1.62; color:var(--c-ink); text-wrap:pretty; }}
.small {{ font-size:var(--t-small); font-weight:400; line-height:1.55; color:var(--c-muted); }}
.num-hl {{ font-family:var(--font-display); font-weight:600; color:var(--c-accent-ink); white-space:nowrap; }}

.head {{ display:flex; flex-direction:column; gap:11px; flex:0 0 auto; }}
.rule {{ height:1px; background:var(--c-line); flex:0 0 auto; }}
.rule--accent {{ height:2px; background:var(--c-accent); }}

/* ---------------- 布局 ---------------- */
.cols {{ display:flex; flex-direction:row; gap:var(--gap); align-items:stretch; }}
.col {{ display:flex; flex-direction:column; gap:14px; flex:1 1 0; min-width:0; }}
.card {{
  background:var(--c-card); border:1px solid var(--c-line); border-radius:var(--radius);
  padding:var(--pad); display:flex; flex-direction:column; gap:12px; min-width:0;
}}
.card--flat {{ background:var(--c-card-alt); border-color:var(--c-hairline); }}
.card--accent {{ border-left:3px solid var(--c-accent); }}
/* 顶边色相：色值走自定义属性，类级规则才能被润色层覆盖（内联 border-top 是盖不掉的） */
.card--topline {{ border-top:3px solid var(--topline, var(--c-line)); }}
.card-top {{ display:flex; flex-direction:column; gap:13px; flex:1 1 auto; min-height:0; }}
.card-metric {{
  display:flex; flex-direction:row; align-items:baseline; gap:8px; flex:0 0 auto;
  margin-top:auto; padding:14px 18px; background:var(--c-card-alt);
  border-radius:calc(var(--radius) * 0.62); border:1px solid var(--c-hairline);
}}
.card-num {{
  font-family:var(--font-display); font-size:var(--t-stat-sm); font-weight:600;
  line-height:normal; letter-spacing:-.02em; color:var(--c-accent-ink);
}}
.card-unit {{ font-size:var(--t-h3); font-weight:600; color:var(--c-muted); line-height:1.3; }}
.card-ghost {{
  font-family:var(--font-display); font-size:var(--t-index); font-weight:600;
  line-height:normal; color:var(--hue, var(--c-accent-soft)); letter-spacing:-.04em;
  margin-top:auto; text-align:right;
}}
.chip {{
  align-self:flex-start; background:var(--c-accent-soft); color:var(--hue, var(--c-accent-ink));
  border-radius:999px; padding:6px 14px; font-size:var(--t-kicker);
  font-weight:600; letter-spacing:.08em; line-height:1.4;
}}

/* ---------------- 要点流：条目均分剩余高度，绝不浮在空白里 ---------------- */
/* flex-shrink 必须是 0：1 1 0 会把条目压到比正文还矮，文字不裁切、直接溢出框外
   压住下一条——lint 报的是「文字重叠」，看着像两条要点串行了。条目只长不缩，
   装不下由 compose 收字号或换版式解决。 */
.bullets {{ display:flex; flex-direction:column; flex:1 1 auto; min-height:0; }}
.b-item {{
  display:flex; flex-direction:row; gap:24px; align-items:center;
  flex:1 0 auto; padding:16px 0; border-bottom:1px solid var(--c-hairline);
}}
.b-item:last-child {{ border-bottom:none; }}
.b-no {{
  font-family:var(--font-display); font-size:var(--t-h2); font-weight:600;
  color:var(--hue, var(--c-accent-ink)); line-height:normal; flex:0 0 auto; min-width:52px;
}}
.b-text {{ flex:1 1 auto; min-width:0; font-size:var(--t-body); line-height:1.62; color:var(--c-ink); text-wrap:pretty; }}
/* 条目少的时候改用「实底横幅」：给每条要点真实版面重量，而不是把行距拉大 */
.bullets--band {{ gap:14px; }}
.b-band {{
  display:flex; flex-direction:row; gap:26px; align-items:center;
  flex:1 0 auto; padding:0 30px;
  background:var(--c-card); border:1px solid var(--c-line);
  border-left:3px solid var(--c-accent); border-left-color:var(--rail, var(--c-accent));
  border-radius:var(--radius);
}}
.b-band .b-no {{ font-size:var(--t-h1); min-width:76px; }}
.b-band .b-text {{ font-size:var(--t-lead); line-height:1.58; }}


/* ---------------- 指标聚焦：一条轨道胜过三张等权大卡 ---------------- */
.metric-spot {{ display:flex; flex-direction:row; gap:46px; align-items:stretch; flex:1 1 auto; min-height:0; }}
.metric-hero {{
  flex:0 0 auto; width:40%; min-width:0;
  display:flex; flex-direction:column; justify-content:center; gap:16px;
}}
/* 指标名 + 小字形：图标只做「名字前面的一个记号」，不做图标块 */
.metric-name {{ display:flex; flex-direction:row; align-items:center; gap:8px; min-width:0; }}
.metric-icon {{ flex:0 0 auto; width:20px; height:20px; }}
.metric-label {{ flex:0 0 auto; font-size:var(--t-h3); font-weight:600; color:var(--c-ink); line-height:1.4; }}
.metric-big {{
  font-family:var(--font-display); font-size:var(--t-stat); font-weight:600;
  flex:0 0 auto; line-height:1.2; letter-spacing:-.03em; color:var(--hue, var(--c-ink)); white-space:nowrap;
}}
.metric-unit {{ font-family:var(--font-body); font-size:var(--t-h2); font-weight:600; color:var(--c-muted); margin-left:10px; }}
.metric-from {{ flex:0 0 auto; font-size:var(--t-small); color:var(--c-muted); line-height:1.5; }}
.metric-notes {{ flex:1 1 auto; min-width:0; display:flex; flex-direction:column; justify-content:center; }}
.metrics {{ display:flex; flex-direction:column; gap:34px; flex:1 1 auto; min-height:0; justify-content:center; }}
.metric-row {{ display:flex; flex-direction:row; align-items:center; gap:26px; flex:0 0 auto; }}
/* 仪表盘：一行一个指标，五到六项也能一页看全 */
.metrics--lines {{ gap:12px; }}
.metric-row--line {{ flex:1 1 0; min-height:0; align-items:center; }}
.metric-row--line .metric-head {{ width:220px; justify-content:center; }}
.metric-row--line .metric-delta {{ width:170px; }}

.metric-bars {{ flex:1 1 auto; display:flex; flex-direction:column; gap:7px; min-width:0; }}
.metric-bar {{
  position:relative; height:11px; border-radius:6px;
  background:var(--c-card-alt); border:1px solid var(--c-line);
}}
.metric-bar-fill {{ position:absolute; left:0; top:0; bottom:0; border-radius:6px; }}
.metric-foot {{
  flex:0 0 auto; display:flex; flex-direction:column; gap:7px;
  padding-top:16px; border-top:1px solid var(--c-hairline);
}}
.metric-foot .item {{ font-size:var(--t-small); color:var(--c-muted); line-height:1.55; }}
.metric-bar-tags {{
  display:flex; flex-direction:row; justify-content:space-between;
  font-size:var(--t-small); color:var(--c-muted); line-height:1.4;
}}
.metric-head {{ flex:0 0 auto; width:200px; display:flex; flex-direction:column; justify-content:center; gap:4px; }}
.metric-value {{
  font-family:var(--font-display); font-size:var(--t-h2); font-weight:600;
  flex:0 0 auto; line-height:1.28; color:var(--hue, var(--c-ink)); white-space:nowrap;
}}
.metric-row .metric-unit {{ font-size:var(--t-small); margin-left:6px; }}
.metric-delta {{ flex:0 0 auto; width:150px; text-align:right; font-size:var(--t-small); color:var(--c-muted); line-height:1.4; }}
/* 轨道：底是量程，实心是当前值，竖线是起点。三者都是可映射的直角/圆角矩形。 */
.metric-track {{
  position:relative; flex:0 0 auto; height:14px; border-radius:7px;
  background:var(--c-card-alt); border:1px solid var(--c-line);
}}
/* 横排时轨道吃掉剩余宽度；竖排（hero）里它是定高的一条 */
.metric-row .metric-track {{ flex:1 1 auto; }}
.metric-fill {{ position:absolute; left:0; top:0; bottom:0; border-radius:7px; background:var(--hue, var(--c-accent)); }}
.metric-mark {{ position:absolute; top:-5px; bottom:-5px; width:2px; background:var(--c-muted); }}

/* ---------------- 大数字 ---------------- */
/* 大数字行用 subgrid 共享行轨道：各卡数字字号不同、说明行数不同，
   各自居中会让数字和色条落在四个高度上，整行看着是散的。
   上下两条 1fr 轨道把内容整体居中，卡片仍然撑满。 */
.stats {{
  display:grid; grid-template-rows:minmax(0,1fr) auto auto auto minmax(0,1fr);
  column-gap:var(--gap); row-gap:16px;
  align-items:stretch; flex:1 1 auto; min-height:0;
}}
.stat {{
  min-width:0; background:var(--c-card); border:1px solid var(--c-line);
  border-radius:var(--radius); padding:var(--pad);
  display:grid; grid-row:1 / -1; grid-template-rows:subgrid; row-gap:16px;
}}
.stat > :nth-child(1) {{ grid-row:2; align-self:end; }}
.stat > :nth-child(2) {{ grid-row:3; }}
.stat > :nth-child(3) {{ grid-row:4; align-self:start; }}
.stat .card-ghost {{ margin-top:0; text-align:left; }}
.stat-num {{
  font-family:var(--font-display); font-size:var(--t-stat); font-weight:600;
  line-height:normal; letter-spacing:-.02em; color:var(--hue, var(--c-accent-ink));
  white-space:nowrap;
}}
.stat-unit {{ font-family:var(--font-body); font-size:var(--t-h3); font-weight:600; color:var(--c-muted); }}
.stat-label {{ font-size:var(--t-h3); font-weight:600; color:var(--c-ink); line-height:1.45; text-wrap:balance; }}
.stat-note {{ font-size:var(--t-small); color:var(--c-muted); line-height:1.55; }}

/* ---------------- 横条 / 柱状 ---------------- */
.chart-wrap {{ display:flex; flex-direction:column; gap:18px; flex:1 1 auto; min-height:0; }}
.chart-head {{ display:flex; flex-direction:row; align-items:baseline; gap:14px; flex:0 0 auto; }}
.chart-note {{ font-size:var(--t-small); color:var(--c-faint); line-height:1.5; }}
/* 折线：SVG 只是截图孪生，pptx 侧是原生连接符 */
.spark-wrap {{ display:flex; flex-direction:column; flex:1 1 auto; min-height:0; gap:12px; }}
.spark {{ position:relative; flex:1 1 auto; min-height:0; }}
.spark svg {{ display:block; width:100%; height:100%; }}
.spark-values {{
  flex:0 0 auto; display:flex; flex-direction:row; justify-content:space-between;
  font-size:var(--t-small); font-weight:600; color:var(--c-ink); line-height:1.4;
}}
.spark-axis {{
  flex:0 0 auto; display:flex; flex-direction:row; justify-content:space-between;
  font-size:var(--t-small); color:var(--c-muted); line-height:1.4;
}}

.chart-delta {{
  font-family:var(--font-display); font-size:var(--t-small); font-weight:600;
  line-height:1.5; color:var(--hue, var(--c-ink)); padding:3px 12px; border-radius:999px; background:var(--c-card-alt);
}}
.bars {{ display:flex; flex-direction:column; flex:1 1 auto; min-height:0; }}
.bar-row {{ display:flex; flex-direction:row; align-items:center; gap:20px; flex:1 1 0; min-height:0; }}
.bar-label {{ flex:0 0 auto; width:190px; font-size:var(--t-body); color:var(--c-ink); line-height:1.4; }}
.bar-track {{ position:relative; flex:1 1 auto; height:34px; background:var(--c-card-alt); border-radius:8px; }}
.bar-fill {{ position:absolute; left:0; top:0; height:34px; border-radius:8px; background:var(--c-accent); }}
.bar-value {{
  flex:0 0 auto; width:132px; text-align:right; font-family:var(--font-display);
  font-size:var(--t-h2); font-weight:600; color:var(--c-ink); line-height:1.3;
}}
.columns {{ display:flex; flex-direction:row; align-items:flex-end; gap:var(--gap); flex:1 1 auto; min-height:0; }}
.col-item {{ flex:1 1 0; min-width:0; height:100%; display:flex; flex-direction:column; justify-content:flex-end; align-items:center; gap:12px; }}
.col-value {{ font-family:var(--font-display); font-size:var(--t-h3); font-weight:600; color:var(--c-ink); line-height:1.3; }}
.col-bar {{ width:100%; max-width:118px; border-radius:10px 10px 0 0; background:var(--c-accent); flex:0 0 auto; }}
.col-base {{ height:1px; background:var(--c-line); flex:0 0 auto; }}
.col-label {{ font-size:var(--t-small); color:var(--c-muted); line-height:1.35; text-align:center; flex:0 0 auto; }}
.chart-legend {{ display:flex; flex-direction:column; flex:1 1 auto; min-height:0; align-self:stretch; }}
.lg-item {{ display:flex; flex-direction:row; align-items:center; gap:14px; flex:1 1 0; min-height:0; }}
.lg-dot {{ width:12px; height:12px; border-radius:4px; flex:0 0 auto; }}
.lg-label {{ flex:0 0 auto; width:150px; font-size:var(--t-body); color:var(--c-ink); line-height:1.4; }}
.lg-track {{ position:relative; flex:1 1 auto; height:8px; background:var(--c-card-alt); border-radius:4px; }}
.lg-fill {{ position:absolute; left:0; top:0; height:8px; border-radius:4px; background:var(--c-accent); }}
.lg-value {{
  flex:0 0 auto; width:118px; text-align:right; font-family:var(--font-display);
  font-size:var(--t-h3); font-weight:600; color:var(--c-ink); line-height:1.3;
}}
.donut-wrap {{ position:relative; flex:0 0 auto; }}
.donut-center {{ position:absolute; left:0; width:100%; text-align:center; display:flex; flex-direction:column; gap:4px; }}

/* ---------------- 时间轴：轨道在外，内容做成实底横幅 ---------------- */
.timeline {{ display:flex; flex-direction:column; gap:12px; flex:1 1 auto; min-height:0; }}
.tl-item {{ display:flex; flex-direction:row; gap:20px; align-items:stretch; flex:1 0 auto; }}
.tl-rail {{ position:relative; flex:0 0 auto; width:16px; }}
.tl-dot {{
  position:absolute; left:1px; top:calc(50% - 7px); width:14px; height:14px; border-radius:999px;
  background:var(--c-accent);
}}
.tl-line {{ position:absolute; left:7px; top:0; bottom:-12px; width:2px; background:var(--c-line); }}
.tl-band {{
  flex:1 1 auto; min-width:0; display:flex; flex-direction:row; align-items:center; gap:26px;
  padding:0 28px; background:var(--c-card); border:1px solid var(--c-line);
  border-radius:var(--radius);
}}
.tl-time {{
  flex:0 0 auto; width:168px; font-family:var(--font-display); font-size:var(--t-h2);
  font-weight:600; color:var(--hue, var(--c-accent-ink)); line-height:1.4;
  display:flex; align-items:center;
}}
/* 正文必须是普通块级：highlight() 会把数字包成 <span>，一旦这里是 flex，
   句子就被拆成「前半句 | 数字 | 后半句」三个并排的 flex item，各自折行，
   中间还留一个洞。垂直居中交给 .tl-band 的 align-items:center。 */
.tl-text {{
  flex:1 1 auto; min-width:0; font-size:var(--t-lead); line-height:1.58; color:var(--c-ink);
}}

/* ---------------- 引用 ---------------- */
.quote {{ display:flex; flex-direction:row; gap:40px; align-items:stretch; flex:1 1 auto; min-height:0; }}
.quote-rail {{ flex:0 0 auto; width:6px; border-radius:3px; background:var(--c-accent); }}
.quote-body {{ flex:1 1 auto; min-width:0; display:flex; flex-direction:column; justify-content:center; gap:30px; }}
.quote-text {{ font-family:var(--font-display); font-size:var(--t-quote); font-weight:700; line-height:normal; color:var(--c-ink); letter-spacing:-.01em; }}
.quote-by {{ font-size:var(--t-h3); font-weight:600; color:var(--c-accent-ink); letter-spacing:.04em; line-height:1.5; }}
.quote-role {{ font-size:var(--t-small); color:var(--c-muted); line-height:1.5; }}
.quote-credit {{ display:flex; flex-direction:column; gap:6px; flex:0 0 auto; }}

/* ---------------- 表格 ---------------- */
.dtable {{
  display:flex; flex-direction:column; border-radius:var(--radius); overflow:hidden;
  border:1px solid var(--c-line); flex:1 1 auto; min-height:0;
}}
.dtr {{ display:flex; flex-direction:row; align-items:stretch; flex:1 1 0; min-height:0; }}
.dtr:first-child {{ flex:0 0 auto; }}
.dth, .dtd {{
  flex:1 1 0; min-width:0; padding:15px 20px; font-size:var(--t-body); line-height:1.5;
  border-bottom:1px solid var(--c-hairline); display:flex; align-items:center;
}}
.dth {{ background:var(--c-card-alt); color:var(--c-muted); font-weight:600; letter-spacing:.03em; font-size:var(--t-small); }}
.dtd {{ background:var(--c-card); color:var(--c-ink); }}
.dtr:last-child .dtd {{ border-bottom:none; }}

/* ---------------- 页脚 ---------------- */
.footer {{
  position:absolute; left:var(--margin); right:var(--margin); bottom:24px; height:24px;
  display:flex; flex-direction:row; align-items:center; gap:16px;
}}
.foot-no {{
  font-family:var(--font-display); font-size:var(--t-kicker); font-weight:600;
  color:var(--c-faint); letter-spacing:.1em; flex:0 0 auto; line-height:1.5;
}}
.foot-src {{
  flex:1 1 auto; min-width:0; font-size:var(--t-kicker); color:var(--c-faint);
  line-height:1.5; text-align:right;
}}

/* ---------------- 封面 / 章节 / 结尾 ---------------- */
.cover-wrap {{ display:flex; flex-direction:column; height:100%; }}
.cover-title {{
  font-family:var(--font-display); font-size:var(--t-cover); font-weight:700;
  line-height:normal; letter-spacing:-.02em; color:var(--c-ink); text-wrap:balance;
}}
.cover-sub {{ font-size:var(--t-lead); color:var(--cover-muted, var(--c-muted)); line-height:1.6; }}
/* 封面/结尾的次要文字落在渐变上，色值由 compose 按最难读的色标反推 */
.cover-wrap .kicker, .end-wrap .kicker {{ color:var(--cover-accent-ink, var(--c-accent-ink)); }}
.cover-meta .small, .end-wrap .small {{ color:var(--cover-muted, var(--c-muted)); }}
.cover-meta {{ display:flex; flex-direction:row; gap:30px; align-items:center; }}
/* 封面数字条：等宽三栏 + 共享行轨道。
   直接复用 .stat 会踩两个坑——它在 .stats 里靠 5 轨 subgrid 定位，flex 父级下
   subgrid 失效，nth-child(1) 的 grid-row:2 会空出一条幽灵行；而 flex 的
   内容定宽让长标签那张卡宽出一倍多，整行右边空一大片。 */
.cover-stats {{
  display:grid; grid-auto-flow:column; grid-auto-columns:1fr;
  grid-template-rows:auto auto; column-gap:var(--gap); row-gap:12px;
}}
.cover-stats .stat {{ grid-row:1 / -1; grid-template-rows:subgrid; row-gap:12px; }}
.cover-stats .stat > :nth-child(1) {{ grid-row:1; align-self:end; }}
.cover-stats .stat > :nth-child(2) {{ grid-row:2; align-self:start; }}
.sec-wrap {{ display:flex; flex-direction:row; gap:56px; align-items:stretch; height:100%; }}
.sec-left {{ flex:0 0 40%; min-width:0; display:flex; flex-direction:column; justify-content:center; gap:18px; }}
.sec-index {{
  font-family:var(--font-display); font-size:var(--t-index); font-weight:600;
  line-height:normal; color:var(--c-accent-ink); flex:0 0 auto; letter-spacing:-.03em;
}}
.sec-title {{ font-family:var(--font-display); font-size:var(--t-h1); font-weight:700; line-height:normal; color:var(--c-ink); text-wrap:balance; }}
.sec-list {{ flex:1 1 auto; min-width:0; display:flex; flex-direction:column; justify-content:center; }}
.sec-item {{
  display:flex; flex-direction:row; gap:20px; align-items:center;
  flex:1 1 0; min-height:0; max-height:96px; border-bottom:1px solid var(--c-hairline);
}}
.sec-item:last-child {{ border-bottom:none; }}
.sec-no {{
  flex:0 0 auto; width:44px; font-family:var(--font-display); font-size:var(--t-h3);
  font-weight:600; color:var(--c-accent-ink); line-height:normal;
}}
.sec-name {{ flex:1 1 auto; min-width:0; font-size:var(--t-lead); color:var(--c-ink); line-height:1.5; }}
.end-wrap {{ display:flex; flex-direction:column; justify-content:center; gap:26px; height:100%; }}
.end-title {{ font-family:var(--font-display); font-size:var(--t-cover); font-weight:700; line-height:normal; color:var(--c-ink); letter-spacing:-.02em; text-wrap:balance; }}
.end-sub {{ font-size:var(--t-lead); color:var(--cover-muted, var(--c-muted)); line-height:1.62; }}

/* ---------------- 母题装饰 ---------------- */
.m-orb {{ position:absolute; border-radius:999px; background:var(--c-accent-soft); }}
.m-orb--sm {{ position:absolute; border-radius:999px; background:var(--c-accent); }}
.m-gridline {{ position:absolute; background:var(--c-hairline); }}
.m-corner {{ position:absolute; background:var(--c-accent); }}
.m-bar {{ position:absolute; background:var(--c-accent); }}

/* ---------------- 来源附录 ---------------- */
/* 页脚是绝对定位在版心底部的：给列表留出那条带子，否则最后一条会压在页码上 */
.appendix {{ display:flex; flex-direction:column; flex:1 1 auto; min-height:0; margin-bottom:52px; }}
/* 条目按可用高度均分：来源多的时候（9 条）固定 84px 会把最后一页顶出画布 */
.apx-item {{
  display:flex; flex-direction:row; gap:18px; align-items:center;
  flex:1 1 auto; min-height:44px; border-bottom:1px solid var(--c-hairline);
}}
.apx-item:last-child {{ border-bottom:none; }}
.apx-no {{
  flex:0 0 auto; width:40px; font-family:var(--font-display); font-size:var(--t-h3);
  font-weight:600; color:var(--c-accent-ink); line-height:1.5;
}}
.apx-title {{ flex:0 0 38%; min-width:0; font-size:var(--t-body); color:var(--c-ink); line-height:1.5; }}
.apx-url {{ flex:1 1 auto; min-width:0; font-size:var(--t-small); color:var(--c-faint); line-height:1.5; }}
"""


def root_style(variables: dict[str, str], cover_bg: str) -> str:
    """:root 变量声明 + 封面渐变（渐变单独给，因为它不是单色）。"""
    lines = [f"{name}:{value};" for name, value in variables.items()]
    lines.append(f"--cover-bg:{cover_bg};")
    return ":root{" + "".join(lines) + "}"