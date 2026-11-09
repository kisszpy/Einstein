# Einstein 前端 UI/UX 评审报告

> 评审日期：2026-11-10 ｜ 评审方式：Playwright 实测截图（19 张，1440×900@2x 明暗双主题 + 390×844 移动端）× 全量源码走查（web/src 约 18k 行）× 生产构建实测
> 评审团：UI 设计全栈专家团（视觉架构师 → 体验设计师 → 前端工程师）
> 截图存档：`.workbuddy/ui-review/shots/`

---

## P0 问题速览（建议本周内修）

| # | 问题 | 层面 | 证据 |
|---|------|------|------|
| 1 | 亮色主题品牌色对比度仅 1.84:1，品牌色文字在浅底上不可读 | 视觉 | globals.css 亮色 `--brand`；light-*.png 全系列 |
| 2 | 流式聊天每个 delta 触发全消息列表重渲染 + 全量 Markdown 重解析，长会话必然卡顿 | 工程 | use-chat-stream.ts:95-99；Markdown.tsx 无 memo；全项目 0 处 React.memo |
| 3 | 单 JS chunk 815KB（gzip 235KB），登录页也要下载 SlideCanvas/DeckEditor 等重模块 | 工程 | vite build 实测；router.tsx 全静态 import |
| 4 | 窄屏下侧边栏与会话列表直接消失，功能不可达 | 体验 | AppLayout.tsx:102、ChatPage.tsx:329；无移动端实拍可用 |
| 5 | 删除会话零确认，误触即丢数据 | 体验 | ChatPage.tsx:295-304 |
| 6 | 22 处 window.prompt/alert/confirm，其中 MembersCard 用 prompt 明文输入密码 | 体验/工程 | grep 实测 22 处 |

---

## 一、视觉设计意见

### 现状总评
登录页完成度很高（神经场 Canvas、锥形渐变描边、打字机 ticker、CapsLock 提示），有明确的"AI 工作台"气质；但**整体视觉体系"头重脚轻"**——登录页精雕细琢，主功能页（尤其 ops/eval/admin）仍停留在工具类默认样式，且 token 纪律不严。

### 问题清单

**P0**
- **品牌色对比度不达标**：亮色主题下 `--brand` 直接用于文字时对比度仅 1.84:1（WCAG AA 要求 4.5:1），次要文字 2.88:1。所有 `text-brand`、`text-tertiary` 在亮色下基本不可读。
- **品牌色自相矛盾**：favicon 与 App 图标是蓝 `#2f6bdc`，界面品牌色却是青绿系。用户从浏览器标签到界面会感到割裂。二选一定稿。

**P1**
- **字号阶梯塌陷**：大量 11px / 11.5px / 12px 正文级文字（登录页 label 11px、聊天页会话摘要、侧栏分组），中文在 11px 下笔画糊成一团。正文最小应到 12.5-13px，辅助文字 12px 封底。
- **圆角与阴影 token 未收敛**：`rounded-[26px]/[25px]/[10px]/[15px]/[52px]` 等任意值遍布，globals.css 426 行中大量一次性变量。应收敛为 4-5 档 radius token + 2-3 档 shadow token。
- **明暗主题不对称**：暗色主题打磨细（surface 分层、backdrop-blur），亮色主题多处直接沿用暗色思路导致发灰、层次弱。

**P2**
- 登录页动效过密（轨道光点 + 扫描光 + 光斑漂移 + 打字机 + 按钮 sheen 同屏 5 类），建议保留 2 类，其余降为一次性入场动画。
- lucide 图标 stroke 混用 1.7 / 1.8 / 2，建议统一 1.75。
- tabular-nums 用得好（登录页、版本号），但 kb/ops 的数字表格未统一。
- 空状态视觉（聊天页"有什么可以帮你？"）不错，但建议推荐问题 chips 加 icon 提升可扫读性。

### 视觉方向原则（定稿建议）
1. **一个品牌色，两套明度**：青绿或蓝定稿一个，`--brand`（交互）/`--brand-strong`（浅底文字）双 token，杜绝裸用。
2. **字阶三级制**：13.5 正文 / 12.5 辅助 / 11 仅限大写微标签，写进 token 禁止任意值。
3. **登录页是门面，内页是工地**：把登录页 30% 的打磨预算匀给 chat/kb 两个高频页。
4. **动效只服务状态转换**（入场、加载、流式），装饰性常驻动画只留登录页一处。

---

## 二、体验设计意见

### P0
- **窄屏功能黑洞**：`hidden md:flex` 直接砍掉侧栏与会话列表，无抽屉、无汉堡入口，1280 以下分屏用户无法切换功能。
- **删除即失去**：删除会话无二次确认、无 undo。配合下方 P1 的"流式中切换被吞"，会话管理是当前体验最脆的一环。

### P1（按用户影响排序）
1. **流式中点击历史会话被静默吞掉**（use-chat-stream.ts:27）：点了没反应，用户以为"坏了"。至少给行内提示"正在生成中，请先停止"。
2. **"联网·自动"三态循环按钮**（ChatPage.tsx:526-539）：单按钮点击循环 联网→自动→关闭，状态不可见、不可预期。改三段 segmented control。
3. **每个 delta 强制 smooth scrollIntoView**：用户流式中无法上滚回看，被强行拽回底部。改为"贴底才跟随 + 底部浮动『回到最新』按钮"。
4. **MembersCard 用 prompt 明文输密码**：安全 + 可用性双输。改带显隐切换的对话框。
5. **搜索请求竞态**（ChatPage.tsx:115-118）：防抖后无取消，快速输入时旧结果覆盖新结果。
6. **WorkspaceSwitcher 切换工作区整页 reload**：所有客户端状态清零，重走 boot。改 react-query invalidate 后无刷新切换。
7. **信息架构**：侧栏"专家·技能·MCP"一项对应路由 experts/skills/mcp 三个路径、渲染同一个 HubPage——用户从不同入口进来看到同一个页面会怀疑"没生效"。要么真合并为一个概念，要么拆开三个真页面。
8. **SSE 无断线策略**：闪断直接 error 终止、已生成文本丢失。务实方案：保留已收文本 + 错误条 + "继续生成"按钮（走 regenerate，纯前端可做）。
9. **响应式空白区**：kb 的表格、ops 的图表在窄屏无横向滚动兜底（代码走查结论，无实拍验证——建议补 768px 截图回归）。

### P2
- 401 跳登录保留了 `?next=` 参数，好；但登录成功后的 640ms 人为延迟可去掉。
- `BootScreen` spinner 文案"正在校验登录状态"出现超过 1s 时应换骨架屏。
- composer 的 Enter/Shift+Enter 提示埋在 placeholder 里，建议发送按钮旁常驻快捷键提示。
- 各页"删除/复制/刷新"交互模式不一：有的 confirm、有的直接执行、有的无反馈，建议统一交互契约。

### 如果只修 5 件事
1. 删除会话加确认
2. 窄屏抽屉（侧栏 + 会话列表）
3. 流式中切换会话给反馈（不吞事件）
4. 联网开关改 segmented control
5. 流式滚动改为贴底跟随

### 体验设计原则
1. **破坏性操作必须可逆**（confirm 或 undo，二选一必须有）。
2. **每个用户动作都要有可见响应**（哪怕只是"正在生成，无法切换"）。
3. **状态可见、可预期**：三态开关用三段控件，流式状态用显式停止按钮。

---

## 三、前端工程意见

> 事实修正：实际技术栈为 React 18.3.1 + Vite 6.4.5（标准版，非 rolldown）。

### 性能

**1. 流式聊天渲染链（最高优先级）**
每个 SSE delta：`setMessages(prev.map(...))` → ChatPage 整体重渲染 → 所有 MessageBubble 重渲染 → 每条助手消息 `normalizeCitations` 两个全局正则全文扫描 → react-markdown **全文重新 AST 解析**（无增量、无 memo）。长会话后期每个 delta 的代价 ≈ O(全部历史消息)。

优化四件套（零新依赖，按性价比）：
1. `MessageBubble` 加 `React.memo` + `onRegenerate/onEdit` 改 `useCallback` 稳定引用（当前是渲染期新建箭头函数，加了 memo 也会失效）。
2. `Markdown` 组件加 `React.memo`（props 仅 content 字符串）。
3. delta 合帧：累积到 ref，rAF 或 30-50ms 定时器批量 flush（SSE 常单包多 delta，目前一 delta 一 setState）。
4. 滚动：仅贴底（<80px）时跟随，流式中 `behavior:"auto"`。

**不推荐现在上虚拟化**：与 scrollIntoView/底部锚点互相打架，先落地 1-3，消息 >500 再议。

**2. 动效**：NeuralField Canvas 写得好（节点封顶 84、DPR 封顶 2x、hidden 停帧、reduced-motion 单帧）——不动。可砍：两片 680px `blur-[140px]` 光斑（单层约 7MB 显存）降为 80-100px；conic 渐变旋转描边改静态或 hover 触发。全局 CSS 动画均走 transform/opacity ✅。

**3. 数据层**：react-query 形同虚设——Provider 常驻但只有 WorkspaceSwitcher 一个 useQuery，其余 25 个文件手写 `useState+useEffect+fetch`（KbPage 18 处、AgentsPage 15 处），无取消、无缓存、无统一错误态。建议把高频列表页迁 useQuery（依赖已装，边际成本零），顺带解决搜索竞态与 WorkspaceSwitcher reload。

**4. 构建**：`vite build` 单 chunk 815.47KB（gzip 235.83KB），Vite 已发 >500KB 警告。修法：router.tsx 路由级 `React.lazy` + Suspense（约 30 行），预期首屏 JS 降至 ~300KB。lucide 树摇 ✅、无 webfont ✅、CSS 62KB ✅。

### 工程结构

超大文件 Top 5：SlideCanvas 1349 行 / KbPage 1121 行（单函数约 1000 行、29 个 useState）/ DeckEditor 1023 / AgentsPage 927 / ModelsPage 717。KbPage 建议优先拆（列表/上传/检索预览/权限），纯移动代码零风险。

重复实现：
- 弹层遮罩 `fixed inset-0` 手写 **11 处**，全部未用 createPortal、无 focus trap，仅 1 处支持 Escape → 抽 `<Modal>`（Portal + Escape + a11y）一次收敛。
- `window.prompt/alert/confirm` **22 处** → 基于 Modal 抽 ConfirmDialog/PromptDialog 机械替换。
- 下拉"点外关闭"手写 document 监听 → 抽 `useClickOutside`。
- fetch 错误提取散布（message-bubble.tsx:117 私有 readError）→ 挪 `lib/api.ts`。

注意：`use-chat-stream` 的 send/resendFrom 依赖 `[messages, isStreaming, run]`，每次 delta 后回调全部重建——memo 方案必须连带处理，否则形同虚设。

### P0/P1 最小改动方案表

| 问题 | 改动点 | 新依赖 | 风险 |
|---|---|---|---|
| 流式全列表重渲染 | MessageBubble/Markdown memo + useCallback | 无 | 低 |
| 815KB 单 chunk | router 级 React.lazy + Suspense | 无 | 低 |
| 删除会话无确认 | removeConversation 前插 ConfirmDialog | 无 | 低 |
| 22 处原生弹窗 | 抽 Modal/ConfirmDialog/PromptDialog 替换 | 无 | 低 |
| 窄屏侧栏消失 | aside 加 fixed+translate 抽屉态 | 无 | 中（测双栏叠加与滚动锁定）|
| delta 强制滚动 | 贴底判断 + auto behavior | 无 | 低 |
| 亮色对比度 | 只改 globals.css 亮色 --brand/--brand-strong | 无 | 低（走查硬编码白字按钮）|
| 三态联网按钮 | segmented control | 无 | 低 |
| WorkspaceSwitcher reload | 先迁 react-query 再 invalidateQueries | 无（已装）| 中 |
| 小字/token 收敛 | 11px→语义 token，globals.css 收敛 <250 行 | 无 | 中（分页推进）|
| 登录页 blur/conic | 降 blur 半径、conic 改静态 | 无 | 低 |

### 工程原则
1. 流式 UI 黄金法则：让"正在变化的单元"成为唯一重渲染单位。
2. 数据层收敛优先于组件重构。
3. 性能改造坚持零新依赖、每项独立 commit 可单独回归。

---

## 四、汇编结论：修复路线图

**第一批（1-2 天，零新依赖，独立可回归）**
- [ ] MessageBubble/Markdown memo + 稳定回调 + delta 合帧（性能 P0）
- [ ] router 级代码分割（首屏 815KB→~300KB）
- [ ] 删除会话确认
- [ ] 流式滚动贴底跟随 + 流中切换提示

**第二批（3-5 天）**
- [ ] 抽 Modal/ConfirmDialog/PromptDialog，替换 22 处原生弹窗（含明文密码）
- [ ] 亮色主题对比度修正（token 层一次性修）
- [ ] 窄屏抽屉
- [ ] 联网开关 segmented control

**第三批（1-2 周）**
- [ ] react-query 迁移高频页 → 解决竞态、reload、缓存
- [ ] KbPage 拆分 + 字阶/圆角 token 收敛
- [ ] 品牌色定稿（蓝 vs 青绿）+ 登录页动效降级
- [ ] 信息架构决策：专家/技能/MCP 合并或拆分

## 证据索引
- 截图：`.workbuddy/ui-review/shots/`（dark*/light* 各 9 页 + mobile-login）
- 截图脚本：`.workbuddy/ui-review/shoot.mjs`（回归时可重跑）
- 关键代码位置均已在正文标注 `文件:行号`
