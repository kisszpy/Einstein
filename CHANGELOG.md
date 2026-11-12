# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.3.0] - 2026-11-13

### 新增

- **智能设计方向**：没选固定风格时，按大纲内容现定一套视觉世界（明暗 / 主副色相 /
  饱和度 / 疏密 / 母题），不再是六套预置里挑一套。模型只决定气质，配色由代码按
  参数生成并保证对比度（720 组参数全扫零违规，图表色最差 3.34:1）。方向按内容
  指纹缓存在会话里，设计台的 seed 参与指纹，等于「换一版」。风格卡新增
  「智能方向」（`builtin:auto`），上传模板路径不受影响。

- **指标版面**：`metric` 版式把「62%→91%」画成达成轨道——单指标是「大数字 + 轨道 +
  右侧解构」，两三个指标是前后双条，三项以上是仪表盘（一行一项，一页看全）。
  顺带修掉大数字卡把「提升 29 个百分点」的 29 当指标显示的老问题；同一串指标页
  不再连续三张等权大数字卡。

- **真实图表**：从散文里抽内联序列（`1 月 88.2%、89.1%…`、`1—4 月良率 88.2、
  89.1…`、`Q1 82 / Q2 86…`），只用原文数字，不插值；窄区间趋势改用折线，pptx 侧
  是原生直线连接符（每个顶点可单独拖），宽区间仍是柱状。

- **接入 impeccable 设计技能**：按平台的技能系统安装（`gh-76cb1c7b23`，
  Apache-2.0，58 个文件），发布到 `<workspace>/.agent/skills/`，通用智能体也能用。
  生成链路新增三个阶段：
  ① 美感检测——内置 Rust 检测引擎（`tools/impeccable/`，sha256 锁定，二进制不入库），
     62 条规则按幻灯片适用性策展，配色来自模板时不报「AI 配色」；
  ② 润色——技能正文 + `polish` 参考 + 品质地板驱动，模型只改 CSS；白名单校验后
     重实测重体检，退步就整份回退；
  ③ 逐页重排——模型在组件词表内重排内容页（不许新类名/内联样式），结构、文字、
     几何三道闸门，不过就回退合成器版本。

### 修复

- 大数字条目没有单位时（「交付准时率 96」）预览/生成 500：可选正则分组返回
  None，`_fit_number` 直接遍历单位字符串。
- 附录来源多时溢出画布并压住页码：按画布高度算容量、超了分页，并给页脚留出 52px。
- 浅色主题的大数字与封面次要文字对比度不足（2.3~3.3:1）：可读色改为按明暗换
  混合方向，封面文字按渐变里最难读的色标反推。
- 封面副题从词中间劈开：改为冒号后断行；内页标题不再把最后一个字甩到第二行。
- 方向 key 用了内置 `hash()`（每进程随机化），换成 sha1，重启后不变。

## [1.2.0] - 2026-11-11

### 新增

- **产物浏览里 pptx 默认用放映级编辑器呈现**。`BrowserWorkbench` 的「演示」标签
  选中 pptx 时不再直接进结构化编辑器，而是 `DeckCraftStage`（DeckCraft wasm）：
  真母版/版式、动画、放映模式；顶部「放映级 / 结构化」可切回原来的服务端重排版。
  保存链路闭环：编辑器内 Ctrl+S → `deckcraft:save` 字节回传 → base64 → 新端点
  `PUT /api/projects/{id}/file-bytes` 写回项目里的同一份 pptx（32MB 上限、只放行
  成品后缀、路径穿越与坏 base64 拒绝，`tests/test_file_bytes.py` 6 项）；
  写回失败自动降级为本地下载，不丢用户改动。后端 uvicorn 顺带加了 `--reload`

- **PPT 美化专家（砚白 / Vellum）**：`expertType: agent`，分类 06-ContentCreative，
  已注册到【专家中心-我的专家】。核心资产是 Skill `deck-polish`：12 种版式的栅格
  与常见错误（`layout-patterns.md`）、配色结构与 WCAG 对比度算法 + 中文字体栈
  （`color-and-type.md`）、22 项交付前检查清单与百分制评分（`review-checklist.md`）、
  诊断报告模板。头像由 ImageGen 生成（512×512，376KB）

- **DeckCraft fork 自编译：wasm 版中文渲染修复**（`tools/deckcraft`，源码入库）。
  上游 web 构建只嵌 Inter/日文字体，汉字全部画成方框；fork 改三处让它带上中文：
  ① `crates/fonts/build.rs` 新增 `fonts/cjk-manifest.txt`（全目标读取）并放行该家族
  过 wasm 体积过滤器；② `crates/fonts/src/fontdb.rs` 新增 `is_cjk_family()`——
  未安装的中文家族（微软雅黑/宋体/思源黑体等 20+ 写法）改走内置汉字面，不再落到
  Latin-only 的 Calibri；③ 字体资产：Noto Sans CJK SC（OFL-1.1，上游已 pin 的
  commit）子集化（码位范围见下方「变更」里的字符集扩展）、改名 `DeckCraft Han Sans`
  （避开上游排除规则及其测试，合并零冲突），单字重 16.4MB→8.94MB。实测：14 页中文
  演示在无头 Chrome 中完整渲染（标题/数据/缩略图全部正常），宿主桥 ready→open→dirty
  全通。体积：wasm 32→52MB（gzip 11→28MB）。构建入口 `tools/deckcraft/build-web.sh`，
  说明见 `tools/deckcraft/FORK.md`。产品内新增 `/deckcraft` 路由（`DeckCraftPage`
  + `DeckCraftFrame`，官方宿主桥协议，Ctrl+S 保存字节自动回传下载）

- **统一日志与请求追踪**：全站 `print` 换成 `logging`，新增 `X-Request-Id` 中间件把
  请求 ID 塞进 `ContextVar`，日志行统一为 `时间 级别 [request_id] 模块: 消息`，
  uvicorn 自身日志也并入同一格式——排查一个慢请求时能凭一行 ID 串起 DB / 检索 /
  LLM 的全部输出。SSE 场景**刻意不 reset** contextvar：流式响应体是在 `call_next`
  返回之后才迭代的，一 reset 就把这段日志的 ID 弄丢了；启动钩子里再 `force` 重挂一次
  handler——嵌入式起服务时 uvicorn 的 `configure_logging()` 晚于应用导入执行，
  会把自带 handler 装回去，不重挂就是两套格式混在一个 stdout 里

- **svgdeck 编译缓存 + 启动预热**：以前改一页就把整个 deck 重编译（实测 10 页
  0.32s，但每次进程首次编译有 ~1.5s 的 skia/字体冷启动）。现在按「页内容
  SHA-256 + 画布 + 母板」算指纹写入 sidecar，指纹一致直接命中缓存（不再看 mtime，
  重新检出/拷贝不会误判失效）；应用启动时用临时目录预热一次转换器，把冷启动成本
  挪到启动阶段。**没有**做单页增量替换：实测只省 0.25s，却要手工改 `slideN.xml`
  与 `rels`，坏一次就是 pptx 打不开，不划算

- **前端测试基建**：vitest + @testing-library/react + jsdom，`pool: threads` +
  `singleThread`（多 worker 会往系统临时目录写 IPC，受限环境下直接 EPERM，
  现象是「只跑了一个测试文件还静默成功」）。已覆盖 SSE 解析（含 UTF-8 跨字节切分）、
  错误边界、DSL 面板、幻灯片单位换算与就地编辑

- **lint 闸门**：ruff（窄规则集 E4/E7/E9/F/I/W，忽略 E501，避免一次性大改）+ pre-commit
  + CI；`tests/test_route_sync.py` 用 AST 断言「`async def` 路由函数体内必须有
  `await`/`yield`」，防止有人再把同步 IO 写回异步路由

- **前端错误边界**：此前没有任何 `ErrorBoundary`，任意页面组件抛一次错就把整棵
  React 树卸载成白屏，连「回聊天页」都点不了。现在每个懒加载页独立兜底
  （`key=pathname`，换页自动重置，侧栏仍可用）；chunk 加载失败（发布后旧页面
  拿旧 hash）单独提示刷新；错误详情可一键复制，`console.error` 保留组件栈供上报

- **SVG 原生 PPT 管线（svgdeck，基于 ppt-master）**：接入开源 ppt-master
  （75k★，MIT，vendored 于 `tools/ppt-master`，依赖 `skia-pathops`/`uharfbuzz`）
  的 SVG→DrawingML 转换器，新增智能体工具 `svg_compile`：逐页写
  `slides/NN.svg`（1280×720，纯 SVG 1.1）后一次编译成**原生形状** PPTX——
  文本框/矢量形状/渐变/圆角全部保留，PowerPoint/WPS 完全可继续编辑（非位图）。
  **母板页支持**：可选 `slides/master.svg` 合成到每页底层统一视觉规范，
  整页母板背景被转换器提升为原生 slide master 背景（全 deck 共享）。
  编辑链路与 slidep 共用：DSL 清单泛化为多引擎（`set_dsl_manifest`），
  编辑器「DSL 源码」模式对 `engine: "svgdeck"` 同样开放（改 SVG → 轻校验
  → 整体重编译）。deep 引擎提示词新增 SVG 路线指引（文字不自动换行需
  手动拆行等）。转换器缺失时（换机部署）抛 SandboxError，智能体降级
  slidep/ppt_create 路线

- **slidep 产物可编辑（DSL 闭环）**：智能体 `slide_upsert` 生成的 PPT 此前没有
  结构化记录，浏览页编辑器只能启发式解析（editable=partial），第一次保存就会把
  渐变/玻璃卡洗成模板风。现在 upsert 成功后逐页登记 DSL 清单（`.deck/*.json`
  sidecar，`_engine: "slidep"`），DSL 即真相源；浏览页编辑器新增「DSL 源码」模式：
  逐页改源码 → lint 校验（溢出/重叠诊断按 severity 分色）→ 通过后重编译替换该页，
  全链路无损。新端点 `PUT /api/projects/{id}/deck/dsl`（apply=false 只校验）。
  结构化 sidecar（ppt_create 产物）优先，混合生成不覆盖；pptx 被外部改动后清单
  自动作废回退解析。

### 变更

- **「浏览器」页更名为「资产」**（用户：这页装的是产物/文件/演示，叫浏览器不准确）。
  路由 `/browser` → `/assets`，旧路径带查询参数（tab/project/path/url）重定向过去，
  老书签与对话来源深链不失效；侧栏入口同步改名，图标 Globe → Archive。
  连带更新可见文案：预览面板「在浏览器页打开」→「在资产页打开」、对话来源
  「在内置浏览器中打开」→「在资产页打开」、网页视图空状态「内置浏览器」→「网页浏览」。
  端到端脚本（browser-browser / agent-panel / ui-sweep）同步更新，并新增一条
  「旧路径重定向且保留参数」的检查（无头实测 4 项全过）

- **DeckCraft 中文字符集 GB2312 → GBK 全集**（用户反馈生僻人名用字仍是方框）。
  子集码位 8,209 → 24,671（GBK 21,003 汉字 = 中文输入法能打出来的边界，龘/珺/玥
  全部覆盖；通用规范汉字表 8,105 字是其子集），单字重 2.87 → 8.94 MB，wasm
  39 → 52 MB（gzip 18 → 28 MB）。实测：含「龘珺玥犇喆珉翀晟龑頔」的两页演示
  无头渲染全部正常，无方框。CJK 扩展 A/B（𬭚 等）仍不在 GBK，需要时改
  `build-han-subset.py` 的 `code_points()` 重跑（约 0.35 KB/字/字重）

- **知识库页拆分**：`KbPage` 1127 → 212 行，只留布局。状态与请求进
  `use-kb-workspace`（7 类操作的 busy/error/确认框各归各位），视图拆成
  `KbSidebar` / `DocList`（含切片与版本面板）/ `SearchBench` / `CreateKbDialog`，
  领域类型独立成 `kb-types`。拆分以「出错时能不能单独定位」为判据：切片保存失败、
  批量重抓部分失败、检索模式对比不准，三条排查路径本来是混在一起的

- **幻灯片编辑器拆分**：`SlideCanvas` 1349 → 1285、`DeckEditor` 1207 → 1048 行，
  抽出 `slide-primitives`（单位换算 `pct/pt/hex` 与就地编辑 `Editable`）与
  `DeckDslPane`，前者从「必须挂整个画布才能测」变成可单测的纯函数

### 修复

- **产物浏览里打开 pptx 极卡（自激循环）**。根因在 `DeckCraftFrame` 的依赖链：
  调用方 `DeckCraftStage` 传的是内联箭头回调（`onLog={(l) => setLog(...)}`），
  每次父渲染都是新身份 → `useCallback` 出来的 `log`/`open` 跟着变 → `deckUrl`
  的 `useEffect` 每次渲染都重跑 → 重新 `fetch` 并重投这份 pptx；而 `open` 内部
  自身要写一条日志（`log` → `setState`）→ 又触发渲染 → **自激循环**，CPU 打满、
  编辑区卡死。/deckcraft 独立页因为用 `useCallback` 传稳定回调，所以一直是流畅的，
  这正是「同一个引擎、两种表现」的原因。修法：回调统一收进 ref（`log`/`open`
  身份恒定，消息监听只订阅一次）、`deckUrl` 的 effect 只在换文件时投喂、
  加 `openedRef` 去重与请求序号丢弃过期 fetch；`DeckCraftStage` 侧同时把回调
  `useCallback` 化。新增 `DeckCraftFrame.test.tsx` 两项回归（父组件连点重渲染
  不得增加 fetch 次数；同一 `deckUrl` 不重开、换文件才重开）

- **界面里到处露出的 `download?path=…&ticket=…`**。三处一起收掉：状态栏日志原本
  打印整条下载地址（`fetch /api/projects/x/download?path=…&ticket=eyJ…`），改为
  「读取 deck.pptx」；整页编辑器顶部把地址输入框换成**文件名按钮**（点一下才展开
  地址输入框，Esc 收起），不再把 API 地址当标题显示；「整页打开」原来是
  `<a href="/deckcraft?deck=<整条地址>">`，悬停提示与新窗地址栏都挂着这串东西，
  且票据写进地址栏会进历史、刷新即失效——改为 `sessionStorage` 一次性交接
  （`handoff.ts`，60 秒有效），地址栏只留 `/deckcraft`，`?deck=` 仅保留为手工入口。
  连带修一个真 bug：`DeckCraftFrame` 用 `url.split("/").pop()` 当文档名，对下载地址
  得到的是 `download?path=deck.pptx&ticket=…`，会污染编辑器标题与另存文件名——
  新增 `deck-url.ts` 的 `deckFileName()` 统一从 `path` 参数取名。
  新增 `deck-url.test.ts`（4 项）与 `handoff.test.ts`（4 项）

- **`/deckcraft` 页面显示 React Router 的「404 Not Found / Hey developer 👋」**：不是路由
  写错，是**开发服务器的模块缓存陈旧**——Vite 进程在 `router.tsx` 加入 `deckcraft`
  之前就已启动，且没有感知到后续改动，浏览器拿到的还是旧路由表，于是路径匹配失败、
  落到框架自带的开发者提示页。重启 dev server 后 `path: "deckcraft"` 正常生效。
  同时补上顶层 catch-all：新增 `NotFoundPage`（回显当前路径 + 返回对话），
  以后路径打错看到的是产品化 404，而不是框架文案

- **知识库「回滚到此版」是个死按钮**：`rollbackDoc` 在确认框之后多了一行无条件
  `return;`，后面的请求永远不会发出——用户点了确认、界面毫无反应、也没有报错

- **KbRepository 协议里混进了实现**：`Protocol` 类里被粘进一整套 JSON 实现
  （4 个方法体，与外部实现重复定义），ruff F811 抓出来后还原成 `...` 桩

- **事件循环不再被同步 IO 占死（135 个路由）**：全站数据库走同步 psycopg、文件与
  subprocess 也是同步调用，但 172 个路由全是 `async def`——单个慢查询/慢打包就会
  卡住整个事件循环，正在流式输出的对话一起停摆。按 AST 判定把「函数体内无
  `await`/`yield`」的 135 个路由改为同步 `def`，交给 FastAPI 自动调度到线程池
  （含 40 线程，可承受并发同步 DB 查询）；流式端点（chat、agent run 等）保持
  `async def` 不动

- **整包下载不再拖垮全站**：`/archive` 原来把整棵项目树 DEFLATE 进 `BytesIO`
  再同步打包——几百 MB 的项目会同时吃满内存和事件循环（下载期间全站 SSE
  对话一起卡住）。改为落临时文件 + `asyncio.to_thread` 打包 + `FileResponse`
  流式返回（`BackgroundTask` 清理），并按后缀对 png/pptx/zip 等已压缩格式用
  STORED 跳过无效压缩；打包途中单个文件消失只跳过不整包失败

- **产物预览改流式**：非 HTML 产物原来 `read_bytes()` 全量进内存再 `Response`
  返回，预览大视频直接顶爆进程并阻塞事件循环；改用 `FileResponse`（顺带支持
  Range，视频可拖进度条）。HTML 的 sandbox CSP 隔离保持不动

- **PPT 预览导出**：`_slide_images` 的动态派发在 PowerPoint 2007 上解析失败
  （`AttributeError: Open.SaveAs`），改用 `gencache.EnsureDispatch` + 拒呼退避重试；
  导出失败的文件写 mtime 标记缓存，避免每次 load 都白耗几秒重试；只在无其他
  演示文稿打开时 Quit，不误杀用户自己的 PowerPoint 窗口。WPS 兼容注册
  （`PowerPoint.Application` 实为 wpp.exe）的机器上 COM SaveAs 可能始终被拒，
  此时预览不可用，前端自动落到 DSL 模式

- **浏览页只展示产物**：/browser 产物 tab 此前直接拉全量文件树（上限 400 条），
  项目里混进源码、依赖与解包残留时产物被淹没（nocturne-cinema 解包 libatomic.deb
  后 1121 个文件，真正的产物只有 index.html 和 README.md）。新增
  `GET /api/projects/{id}/artifacts`：按 `ARTIFACT_SUFFIXES` 白名单（html/md/pptx/
  pdf/docx/xlsx/png/svg）过滤，且排除 SKIP_DIRS 依赖目录与点/下划线开头的运行时
  目录（.cache/.local/_scratch…）；`project_summary` 与其共用同一套判定。前端
  BrowserWorkbench 改走新端点，侧栏标题/空态文案同步改为「产物」。智能体页左侧
  「工作区」文件树保留全量 /tree（那是智能体工作文件视图，不是浏览页）

- **浏览页计数口径统一**：`/api/projects` 补 `artifacts` 字段（白名单过滤后的
  产物数，与产物清单条数同源），选择器括号、项目卡片、顶栏概览全部改用该字段，
  不再和清单条数打架（nocturne-cinema 此前括号 1121、清单 2 条）；`files` 保留
  全量口径给智能体页工作区视图

- **项目列表 500（网站挂了的根因）**：智能体解包 .deb 在 `nocturne-cinema/libx/` 里留下
  Linux 悬空软链（`libatomic1 -> gcc-14-base`），Windows 对这类条目 stat 抛
  `OSError(WinError 1920)`，`Path.is_file()` 不吞这个错误码直接炸——`/api/projects`
  全量 500，文件页打不开。修复：`sandbox.iter_files()` 统一安全遍历（`os.walk +
  onerror` + 逐条 `_stat_safe`，坏条目跳过），替换 `list_projects`、`dir_size`、
  `write_file` 计数、`project_summary`、`list_files` 树、整包下载、技能打包/发布
  共 7 处裸 `rglob`。回归测试 `tests/test_sandbox_iter_files.py`（模拟 WinError 1920
  + 真实悬空链接双路径，4 用例）。

### 移除

- **任务侧栏底部的「工作区」整块**（项目下拉 + 文件树 + 预览/刷新/新建/下载 zip/
  删除 五个图标按钮）。它是同一件事的第三份入口：项目绑定在输入框「+」能力弹层的
  「项目」分组里已有（含「由智能体创建」与「新建项目」），文件浏览/预览在「浏览器」页
  与同页顶栏的「预览」按钮已有。删掉后任务列表多出约 200–260px 可滚动高度。
  连带清理：`AgentsPage` 的 `tree` state、`loadTree()`、`deleteProject()`、
  `TreeNode` 类型，以及 SSE `done` 分支里那次多余的树刷新。
  **代价**：删除项目的入口随之消失（浏览器页目前只能新建/浏览，不能删）。这是破坏性
  操作，没有随手补在顶栏；需要时在浏览器页项目下拉旁加带二次确认的删除。

- **移除 vendored PPTist 编辑器**（`tools/pptist`，337 MB）。DeckCraft 在母版/版式/
  动画/放映/中文渲染上都已超过它，双编辑器只会让保存链路分叉（一个回结构化 JSON、
  一个回 pptx 字节），因此整条 PPTist 线退场：删 `tools/pptist` 源码与
  `src/embed/{bridge,font-map}.ts` 改造、删前端 `deck-editor/{DeckEditorPage,
  PptEditorFrame,bridge}.tsx`、删 `/deck` 路由与侧栏「幻灯片」入口、清 `.gitignore`
  对应规则。连带影响：**不再有 AGPL-3.0 与 1999/年起商业授权的义务**。
  产物浏览里「演示」标签的「结构化」视图保留（走服务端 DSL 重排版，与 PPTist 无关）。
## [1.1.0] - 2026-11-10

一轮 UI/UX 大修 + 沙箱与专家体系补全。触发点是用户与设计团队两份评审报告
（`docs/UI-UX-走查-2026-11-10.md`、`docs/ui-ux-review-2026-11-10.md`），全部可执行项
落地后经 playwright 全站走查回归（12 路由 × 亮暗，22 页中 20 页零异常）。

### 新增

- **七位内置专家**：前端/后端/测试工程师、高级 UI/交互设计师、产品经理、系统架构师。
  `ensure_presets()` 首次加载幂等种子（同名跳过，删除后不复活），live 状态、
  预置语义（全员可见、仅管理员可改）；人设含开场白、推荐问题与可验收的工作方式，
  工具按角色挂载（产品经理带演示文稿，可产出评审 PPT）
- **交互基建**：`ui/dialog.tsx`（Modal：Portal + Escape + 滚动锁定）与
  `lib/confirm.tsx`（promise 化 `confirmDialog` / `promptDialog` + 全局 DialogHost），
  替换全部 22 处 `window.confirm/alert/prompt`；MembersCard 明文输密码改显隐切换；
  删除会话/知识库/项目/任务/专家/成员等破坏性操作一律确认 + danger 红键
- **联网分段控件**：三态循环按钮改 segmented control（自动/始终/关闭，状态可见可点）
- **流中切换提示**：生成中点历史会话给提示条（带「停止生成」），不再静默吞掉
- **继续生成**：SSE 报错后气泡内提供「继续生成」，已收文本保留不丢
- **窄屏会话抽屉**：聊天页会话列表 <768px 由直接消失改为滑入式抽屉（汉堡入口 + 遮罩）

### 变更

- **流式渲染链重做**：MessageBubble/Markdown `memo` + 稳定回调 + delta rAF 合帧 +
  `normalizeCitations` useMemo——流式时不再逐字符重渲染整个消息列表；
  滚动改贴底跟随（上滚回看不被拽回），离底时浮出「回到最新」
- **路由级代码分割**（React.lazy + Suspense）：首屏 **815KB → 337KB（gzip 236KB → 106KB）**，
  浏览器页重模块按需加载
- **对比度全站达标**：亮色品牌绿文字约 25 处改 `text-brand-deep dark:text-brand`，
  `brand-deep` 加深、tertiary 45%→61%、语义色加深至 ≥4.5:1；170 处 ≤11.5px 小字统一
  12px；9 处小点击目标加高到 ≥24px；admin 操作列裸文字改按钮
- **正文与输入框严格对齐**：助手卡片 85% 宽改满宽同边距，滚动条占位差 10px 用
  `scrollbar-gutter: stable` 消除；撤掉刺眼的输入框品牌绿聚焦描边
- **引用编号上标化**：正文的 `【w1/w2/w4】`、`[W1]` 渲染成 Unicode 上标，代码块与
  链接护栏内不动；联网提示词同步收紧
- **登录页**：`admin123` 提示加 `import.meta.env.DEV` 门禁；成功跳转延迟 640→400ms
- **favicon**：蓝 `#2f6bdc` → 品牌青绿，与界面一致
- **ops 趋势图**：单日数据限宽 + 渐隐，不再整块实心绿

### 修复

- **产物下载 401**：`<a href>` 原生导航带不了 Authorization 头，下载接口返回
  「需要登录」；`downloadUrl` 补短期票据（与预览同机制），ArtifactView 三处、
  DeckEditor pptx、TaskSidebar 整包 zip 全部修复
- **搜索竞态**：防抖请求加 AbortController，旧响应不再覆盖新结果
- **delta 流中断丢字**：合帧缓冲在中断/出错时先落盘已收文本

### 文档

- 全站走查报告、设计团队评审的逐条核实意见（`docs/` 下三份）

## [1.0.0] - 2026-11-09

能上线。0.8.0 之后功能已经够用，剩下的全是「一个人在本机跑得好、交给十个人用就出事」
的问题：内容出不去也进不来（导出/分享/图片）、备份只有脚本没有演练、单进程假设
散在各处（倒排表、队列、运行状态）。这一版把这些逐个拆掉，并给出容量基线。

### 新增

- **会话导出** `GET /api/conversations/{id}/export?format=md|json`：助手正文整体进
  引用块，避免答案里的 `##` 打乱文档层级；文件名按 RFC 5987 编码（中文标题不再乱码），
  纯中文标题的 ASCII 兜底名退化成 `transcript-日期` 而不是一串下划线
- **免登录分享** `shares` 表 + `/s/{token}`：token 既是主键也是凭据，**不引入匿名账号**
  （否则会污染成员表、用量表与审计）。可设 7/30 天过期、随时撤销；知识库分享默认
  **不许匿名追问**——允许等于让任何人花你的 token 钱
- **iframe 嵌入**：分享页可嵌进第三方页面，提供 `<iframe>` 片段；`/api/public/*` 前缀
  被鉴权中间件放行，这两条路由自己按 IP 限流（读 120/分、追问 6/分）
- **OCR 与图片摄取**：`.png/.jpg/.webp/.bmp/.tif` 等直接入库；扫描件 PDF（无文本层）
  按页渲染成图再 OCR。两条路：OpenAI 兼容的多模态端点（`APP_OCR_MODEL`）或本地
  `pytesseract`。都没配时**报一个照着做就能解决的错**，而不是入库一份空文档
- **备份运营化**：`tools/backup.py verify|prune|list`——校验归档可读、manifest 完整、
  sqlite `integrity_check` / json 逐份解析 / pg_dump 含 COPY；保留策略同时按份数与
  天数（连续一周备份失败时，只按份数会把老包全留下）
- **容量报告** `tools/report_capacity.py`：实测一次检索延迟并给出规模，取代拍脑袋
- **运行事件外置** `run_events` 表（迁移 21）：多副本下刷新页面被打到另一个进程，
  也能从第 0 条重放并跟到结束

### 变更

- **BM25 倒排表按库缓存**：原先每次 hybrid 检索都读全库 + 重建倒排表，5000 切片约
  3 秒；现在按 `(库集合, 签名)` 缓存，签名是「切片数 + 最新 created_at」，TTL 300 秒
  兜住就地编辑。本机实测 **3.675s → 1.571s**
- **队列领取原子化**：`BLMOVE pending→inflight` + Lua 标记 running，修掉「BLPOP 与
  改状态之间崩溃 → 任务凭空消失」，崩溃后由 inflight 兜底回收；`complete/fail`
  校验 `worker_id` 与 `running` 状态，旧 worker 不能再覆盖新 worker 的结果
- **`delta` 帧不落库**：一次回答几千帧，每帧一行会把「流式」变成「写数据库」。
  重连时正文从会话历史回来，少一段逐字动画不算信息丢失
- **同步上传的解析丢线程池**：OCR 一页几秒、扫描件几十秒，直接在事件循环里跑会把
  整个 API 卡住
- 版本号 0.8.0 → 1.0.0（`main.py` / `web/package.json`）

### 文档

- 运维手册新增 5.2「多副本」：五项必须共享的依赖（store / vector / queue /
  checkpoint / 限流）逐条给出检查命令与不满足时的后果，以及「Windows 上检查点只能
  退 sqlite，所以多副本只能在 Linux」这一条硬约束
- 运维手册 3.2 / 6.1 / 6.2：花在哪、容量三数、迁移前后校验

### 已知问题

- 本机 `report_capacity.py` 显示 **5258 切片但向量数 0**：Milvus 集合是 1024 维，
  当前嵌入模型写 4096 维，日志持续报维度冲突，检索一直在走内存暴力路径。
  修法见运维手册 6.1：`python tools/rebuild_vector_index.py --reembed --drop`

## [0.8.0] - 2026-11-09

团队与运营。0.7.0 把闭环补齐之后，剩下的问题不是"能不能用"，而是"多人用起来
谁在花钱、谁在出错、这把钥匙归谁管"。这一版把模型、权限、账单、告警四件事
从"写死在 `.env` 里"变成"可配置、可审计、可追责"。

### 新增

- **模型注册表** `models` 表 + `/api/models`（增删改查 + `POST /{id}/test` 真发一次
  `max_tokens=8` 的补全）。解析顺序为 `指定 model_id → 空间默认 → .env`，
  未登记模型时行为与升级前完全一致——不预置任何行
- **按专家选模型**：`experts.model_id`，对话与智能体共用同一份解析（`services/models.py`
  的 `resolve()`），不再两处各写一遍 `base_url/model` 然后慢慢漂移
- **成本看板** `GET /api/obs/cost?days=7`：按天 / 模型 / 成员 / 入口四个视角，
  每桶含 `runs/errors/tokens_in/tokens_out/llm_calls/cost/avg_ms`。`run_traces.cost`
  在 `finish()` 时按当时单价定格——改单价不改历史账
- **告警** `alert_rules` + `alert_events`：四类规则（错误率 / 检索空手率 / 日花费 /
  模型探活失败），webhook 按域名自动选格式（企业微信 / 钉钉 / 飞书 / 通用 JSON），
  带冷却时间与投递留痕（`delivered=false` 在页面上标红）
- **文档源同步**：文档记 `source_url` / `fetched_at`，单篇与批量重抓、过期清单。
  `digest` 未变时只更新 `meta.fetched_at`、**不重嵌入**
- **库级授权** `kb_grants`：`kb_access.access()` 返回 `read|write|None`，
  知识库列表把"授权给我的私有库"并进来并带 `my_role`
- **任务模板** `task_templates` + `/api/templates` + 智能体页模板条：只存起步参数
  （prompt / 专家 / 项目 / 工具 / 知识库），不写死步骤

### 变更

- **知识库读写拆开**（行为变化）：原实现用 `owns()` 同时判读写，"工作区可见"等于
  顺带给全空间写权限。现在读宽写严——18 个写入口单独标 `write=True`，
  预置库（`owner_id=''`）对所有人只读，改内容需显式 write 授权
- 告警评估分两条入口：`evaluate(owner_id=None)` 供后台定时任务（不看归属），
  `evaluate(owner_id=当前用户)` 供界面手动触发（只看自己的规则）
- 新增配置 `APP_KB_STALE_DAYS`（默认 30）、`APP_ALERT_INTERVAL_MINUTES`（默认 10），
  后台 `alert_loop` 与手动检查共用同一份 `evaluate_all()`

### 修复

- **token 从来没进过轨迹**：`traces.note_tokens` 从定义起没有任何调用点，
  `run_traces` 的 token 永远是 0——成本看板会是一张空表。补了 `llm.py` 两处与
  `LlmTimer.__exit__` 一处
- **默认模型跨空间串台**：`resolve()` 的缓存键只有 `model_id`，A 空间解析出的默认
  模型会被 B 空间命中。键改为 `model_id or f"default:{workspace_id}"`，
  `workspace_id` 一路透传到 `chat/agent/agent_deep`
- **冷却时间戳写不进去**：`update_rule()` 有字段白名单，`last_fired_at` 被静默丢弃，
  等于冷却永不生效。新增独立的 `mark_fired()` 写路径——否则任何能调 PATCH 的人
  都能把冷却清零
- **`visibility` 列从未建过**：dataclass 有字段、判定逻辑也在，但 `TableState.write`
  按 `tables.py` 的列声明取值，多出来的字段被静默丢弃，重启即失效。迁移 015 补列

## [0.7.0] - 2026-11-09

把插头插上。这一版不新增能力，只补闭环——审计列出的问题里，绝大多数是
"代码在、开关没通、界面没接"：限流中间件读不到用户、`note_tokens` 没人调用、
断连转后台做完了但前端从不接回。所以逐条跑起来看现象，而不是读代码判断。

### 新增

- **知识库管理界面**：切片列表与编辑、删单篇、单篇重建、版本列表与回滚、失败重试，
  六个接口接上前端。传错文件终于能单独删，不用删整库重传、重付一次嵌入成本
- **三档可见性**：知识库 / 专家 / 技能统一为 `private`（默认）/ `workspace` / `shared`，
  下拉 + 徽标，共享需显式操作
- **检索可信**：重排默认 `auto`；评测支持负例集（`expect_miss`，只测检索层，
  不进生成环节）；报告分出正例 / 负例通过率
- **可信对话**：重新生成（旧答案留一版，且不把上一版喂给模型）、编辑后重发、
  赞踩采集原因
- **运行可见**：`run_traces` 表 + `/api/obs/traces`，前端刷新后自动接回仍在跑的任务

### 变更

- 审批默认覆盖 `code_run`（原先只有显式配置才拦）
- 知识库 / 专家 / 技能的列表接口改为按可见性过滤，默认私有

### 修复

- **限流实际等于全放行**：中间件排在鉴权之前，读不到 `request.state.user`；
  重排到鉴权之后。`quota_incr("requests")` 也补上了调用点——日请求配额原先
  只有 tokens 在累加，`requests` 是个空转开关
- **删库 / 删文档不级联**：`document_versions` 里的正文留在库里，重建时命中旧数据。
  两套仓储四处一起改
- **MCP 凭证是自写 XOR 流**：换为 `enc:v2`（Encrypt-then-MAC，HMAC-SHA256 截断
  16 字节），v1 密文仍可解；篡改不再静默。`APP_ENV=prod` 时拒绝以默认
  `secret_key` 启动

## [0.6.1] - 2026-11-09

短期记忆落盘。0.5.1 把审批迁到了框架的 `interrupt` + `checkpointer`，但检查点
只活在进程内存里——服务一重启，"批准后从断点续跑"就退化成"按批准重跑"，
已写出的文件和已花的 token 全部重做一遍。这次把检查点真正写到磁盘上。

### 新增

- **检查点落盘** `app/services/checkpoints.py`，四档由 `APP_AGENT_CHECKPOINT` 控制：
  `auto`（默认，跟主存储走）/ `sqlite` / `postgres` / `memory` / `off`。
  sqlite 落到 `data/checkpoints.db`（WAL 模式），postgres 落到 `APP_POSTGRES_DSN`
  指向的库（`checkpoint*` 四张表，由框架 `setup()` 建，与业务表不冲突）
- **跨重启续跑**：强杀进程后重启，同一 `thread_id` 的图状态还在，批准后
  从断点继续而不是重跑。验收 `tools/verify_checkpoint_restart.py` 分两段跑，
  中间真的杀进程（10 项断言，含"没有退化成按批准重跑"）
- **检查点 TTL 清理**：`APP_AGENT_CHECKPOINT_TTL_HOURS`（默认 72）过期线程自动
  删除，启动时扫一次 + 每小时一次，退出时关连接（`main.py` 的 lifespan）。
  判据用 checkpoint 自带的 `ts`——看文件时间等于永远删不掉东西
- **可观测性** `GET /api/obs/checkpoints`：返回档位、落点、TTL、是否已连上。
  排查"批准后为什么重跑了"先看这里，`connected=false` 说明没落盘
- **测试** `tests/test_checkpoints.py`（13 项）：档位解析、跨连接读回、线程隔离、
  降级链、TTL 清理。全套 pytest 从 38 项增至 51 项

### 变更

- `APP_AGENT_CHECKPOINT` 默认值从 `memory` 改为 `auto`。本地开发（`store_backend`
  是 json/sqlite）自动用 sqlite，生产 postgres 主库在 Linux 上用 postgres——
  不用记两处配置。**行为变化**：本地重启后不再丢检查点，续跑能接上
- 降级链改为 `postgres → sqlite → memory`（原来是直接掉到 memory）。内存档等于
  "什么都没落"，用户看不到任何异常，直到某天发现任务被重跑了

### 修复

- **Windows 上 postgres 档静默降级**：psycopg 的异步连接要求 selector 事件循环，
  而 uvicorn 在 Windows 上建的是 Proactor（`app.main` 在 loop 内导入，模块级改
  policy 已经来不及）。此前配了 `postgres` 会白等 15 秒连接池超时再掉到内存，
  日志刷一堆 `cannot use the ProactorEventLoop`。现在 `auto` 按平台直接选
  sqlite，显式配 `postgres` 立即失败并说明原因（Linux 不受影响，实测容器内正常）
- **`verify_isolation` 空环境误报**：原来断言"库里必须存在预置专家"，空环境下
  直接 FAIL。改为用"A 也看不到"来区分环境缺数据（SKIP）与隔离真的坏了（FAIL）

## [0.6.0] - 2026-11-09

批次 B/C 的剩余项：让"看"和"跑"脱钩（流式 + 后台执行），把智能体接上长期
记忆，给向量加上可校验的身份，容器收到非 root + 只读根，验收脚本进 pytest。

### 新增

- **智能体真流式**：`astream(stream_mode=["updates","messages"])` 订阅模型增量，
  逐 token 转成 `delta` 事件。首字延迟从"全文生成完"降到"第一个 token"
  （实测 600 字回答：首帧 1.7s / 完成 7.2s，636 帧）。工具调用轮次里模型先说
  的那段话用 `answer_reset` 撤回，不会掺进最终答案
- **运行与 SSE 解耦**：执行放进独立 task，事件写进 `services/run_registry.py`，
  SSE 只是读者。客户端断开（切标签页、网络闪断）不再掐掉任务，跑完照样落库；
  `GET /agent/runs/{run_id}/stream` 可从已有事件重连接回（归属不符 404）
- **长期记忆接入智能体**：装配前按本轮任务召回工作区事实，拼进系统提示
  （`recall_memory` / `memory_block`）。deep 与 react 两个引擎行为一致；记忆
  读失败只降级为"没有背景"，不影响任务
- **浏览器进程复用**：截图不再每次冷启动 Chromium。按线程缓存（Playwright
  同步 API 绑定创建线程），空闲 `APP_BROWSER_IDLE_SECONDS`（默认 120s）回收，
  进程退出时清理；容器里补 `--disable-dev-shm-usage`
- **切片记嵌入指纹**：`chunks` 表加 `embedding_model` / `embedding_dim`（迁移 012）。
  检索前比对当前模型，不一致直接给出可操作提示而不是返回空；`retrieval.Retrieval`
  新增 `error` 字段，与"未命中"分开——前者要去重建索引，后者要补文档
- **重新嵌入**：`tools/rebuild_vector_index.py --reembed --drop`。换模型后光重建
  索引没用（旧向量还在旧空间里），必须重算向量；不重切片，保留边界与父子关系
- **pytest 测试体系**：`server/tests/`（38 项）+ `pytest.ini` + `requirements-dev.txt`，
  CI 增加"单元测试"步骤。测试自己把存储按到 sqlite 临时库，不碰 `.env` 里的
  生产后端

### 变更

- **容器收紧**：`--read-only` + `--tmpfs /tmp,/run` + `--user 1000:1000` +
  `PIP_USER=1`（依赖落到工作区，装包仍可用）。实测容器内 `id -u = 1000`、
  `touch /etc/x` 被拒、`pip install six` 成功。宿主目录属主与 uid 不匹配时
  自动降级为 root 重试一次并打日志（Linux bind mount 的现实约束）
- 三个配置项：`APP_AGENT_STREAM`、`APP_BROWSER_IDLE_SECONDS`、
  `APP_AGENT_PRO_RUN_AS` / `APP_AGENT_PRO_READONLY_ROOT`
- `screenshot` 的页面错误不再连带杀掉浏览器：`new_page` 失败时重建一次

### 修复

- **`_safe()` 把 `ApprovalRequired` 当普通异常吞掉**：react 引擎的审批暂停
  永远触发不了，用户看到的是工具返回一个 `{"ok": false}`，模型自己换路走。
  人工确认在回滚引擎上形同虚设——由新增的 pytest 用例发现
- **审批的"一次性"没落实**：`granted_tools` / `latest_decision` 没过滤
  `consumed`，一次批准在同一会话内持续生效，又变回"批一次永久放行"
- **检索失败伪装成"知识库未命中"**：向量与嵌入模型不匹配时返回空列表，
  用户会以为库里没内容而去重传文档

## [0.5.1] - 2026-11-09

按《评估报告-产品缺陷与改进》修掉批次 A 的 P0：**人工确认从"抛异常当暂停"
换成框架的 interrupt + checkpointer**，检索从"永远命中的 0.01 阈值"换成绝对
相关性门限。行为上最直观的差别是——批准之后从断点继续，而不是把任务从头再
跑一遍。

### 新增

- **人工确认落库** `app/services/approvals.py` + 迁移 011（`agent_approvals` 表）：
  键为 `(workspace, owner, session, tool, args_hash)`，`decision` 记
  approve/reject，`consumed` 保证一次确认只用一次，`expires_at` 默认 30 分钟。
  归属按 `(workspace, owner)` 过滤，别人的 session 查不到任何记录（统一 404，
  不区分"不存在"与"不是你的"）
- **参数指纹** `args_fingerprint(args)`：批准只对**这一组参数**有效。同一工具
  换参数要重新确认，避免"批了一次就等于永久放行这个工具"
- **检索门面** `app/services/retrieval.py`：`retrieve()` 一处收口改写、父片展开、
  门限、文档配额与引用；`/chat` 与 `/agent/run` 共用，两条入口的检索行为不再
  各写一套
- **引用透传**：智能体收尾帧带 `citations`，SSE 新增 `citations` 事件，前端
  在答案下方渲染「参考资料」（编号 / 文档名 / 相关度）
- **验收**：`tools/verify_p0_fixes.py`（34 项，脚本化模型驱动图，验证框架语义）、
  `tools/verify_hitl_e2e.py`（15 项，真实 HTTP + 真实模型验证"暂停→批准→续跑"
  与"拒绝后工具不执行"）

### 变更

- **审批改走 LangGraph interrupt**：`create_deep_agent(interrupt_on=..., checkpointer=...)`，
  中断在 `updates` 流里以 `__interrupt__` 键出现（不是异常）；批准后发
  `Command(resume={"decisions": [...]})` 到同一 `thread_id`（`{ws}:{session}`）。
  批量中断时 `decisions` 个数按 `aget_state().tasks[].interrupts` 对齐，模型一轮
  调两次敏感工具也能正确续跑
- **新增运行状态持久化** `agent_runtime.get_checkpointer()`：`memory`（进程内
  `InMemorySaver`，支持中断续跑，重启后失效）/ `off`（旧行为）。检查点丢失时
  引擎明确告知并退回「按批准重跑」，不假装续跑
- **相关性门限语义修正**：删掉 `MIN_SIMILARITY = 0.01`（任何文本都有 0.2 以上
  相似度，等于没设），改按**绝对分**判定（重排分优先，余弦其次，纯词法命中按
  jieba 词覆盖率 ≥0.5 放行）。候选池先用 `floor * 0.6` 粗剪，排序后用
  `apply_score_gate` 精判；全部被砍时打印最高分，便于按实际嵌入模型标定
- **同文档配额** `cap_per_document(hits, max_per_doc=2)`：一篇结构重复的文档
  不再能占满全部名额，四片全来自同一文件时"命中"其实是假象
- **`execute` 装配期裁剪**：`FilesystemMiddleware(backend=..., tools=[...])`
  按 `supports_execution(backend)` 决定挂不挂 `execute`，做到「工具集 ⊆ 后端
  能力集」。默认引擎不再给模型一个调用即报错的工具；Pro 引擎后端支持执行，
  照常挂上
- **嵌入分批**：`_split_batches(texts, max_items, max_chars)` 双阈值切分，
  `APP_EMBEDDING_BATCH_SIZE=32` / `APP_EMBEDDING_BATCH_CHARS=40000`，整篇长
  文档不再塞进一个请求等超时
- **容器网络默认 `none`**：`APP_AGENT_PRO_NETWORK` 由 `bridge` 改 `none`，需要
  装包时临时改回 bridge
- **项目 slug 空值拒绝**：拆出 `slug_from_name()`（可能为空）与 `slugify()`
  （回落 `untitled`，仅内部路径用）。全中文标点或纯符号的项目名不再静默撞进
  `untitled` 目录

### 修复

- **批准后重跑整个任务**：旧实现用抛异常中断，批准后前端只能把原任务重发一遍，
  已写的文件被再写一遍、已完成的步骤全部重做
- **审批状态存进程内存**：重启即丢、无归属校验（拿到 session_id 就能预批准）、
  永不过期、批一次永久放行
- **"知识库未命中"分支不可达**：阈值形同虚设，不相关片段被当成证据喂给模型
- **Token 日配额只记不拦**：`/chat` 与 `/agent/run` 入口补 `quota_check(ws, "tokens")`
  → 429，`APP_QUOTA_DAILY_TOKENS` 才真正生效
- **向量维度不符静默丢弃**：内存后端遇到维度不匹配时整批过滤且不报错，表现为
  "知识库有内容但检索永远为空"；改为打印丢弃条数与重建索引的命令
- **失败运行不留记录**：`/agent/run` 异常分支补 `status="failed"` 落库，产物已
  写盘却查不到运行记录的情况不再出现

## [0.5.0] - 2026-11-09

在 deepagents 之上加一个带真沙箱的 Pro 引擎，并把三个引擎里"自己实现"的部分
收敛回框架能力（本次以整理为主，对外事件协议不变）。

### 新增

- **Pro 引擎（`APP_AGENT_ENGINE=deepagents-pro`）**：`app/services/agent_deep_pro.py`
  - `ProBackend` 多继承 `SandboxBackendProtocol`，harness 检测到后端支持 execute 就多给模型一个 `execute` 工具；文件边界仍复用 `WorkspaceBackend`
  - 容器模式 `docker run --rm`：项目目录挂 `/work`，`--cap-drop ALL` / `no-new-privileges` / 内存 CPU PID 全限，超时补刀 `docker rm -f`；命令用 `sh -s` 从 stdin 读脚本，绕开 `sh -lc "..."` 的双层引号解析
  - 宿主降级走框架 `LocalShellBackend`（无隔离，结果里带提示）；`APP_AGENT_PRO_SANDBOX=container|local|off` 可控
  - 浏览器工具 `browser_open` / `browser_links` / `browser_shot`，schema 由 `@tool` 从签名生成；截图双后端（Python playwright → 仓库内 playwright-core）
- **公共运行时 `app/services/agent_runtime.py`**：`short` / `build_messages` / `LlmTimer` / `final_frame`，三个引擎共用同一套消息拼装、指标埋点与收尾帧
- **验收**：`tools/verify_deep_pro.py` 27 项（沙箱模式、项目边界、产物登记、浏览器、假模型端到端确认真实执行 execute）

### 变更

- deep 系两个引擎收敛到唯一入口 `_run_deep`：技能同步、装配、消息拼装、执行循环只有一份实现，Pro 只提供"装配回调 + 开场自述"
- ReAct 回滚路径迁到 `langchain.agents.create_agent`（`langgraph.prebuilt.create_react_agent` 已废弃），事件映射不再依赖节点名，只按消息类型判断，并按 id 去重
- 容器执行输出只回原始 stdout/stderr 与退出码，`[Command succeeded/failed with exit code N]` 与截断提示交给 harness，避免两条状态行
- 危险命令正则黑名单只在宿主 shell 模式生效：容器有真隔离，黑名单只会误杀；`APP_AGENT_PRO_NETWORK` 默认仍为 `bridge`（改 `none` 待定）
- 侧栏与登录页的版本号改由 `package.json` 经 Vite `define` 注入（`__APP_VERSION__`），界面不再手写版本串

### 修复

- deep 引擎改名为公开的 `WorkspaceBackend`（Pro 需要继承），`tools/verify_project_isolation.py` 同步

## [0.4.0] - 2026-11-09

多租户边界从「空间级」下沉到「人」：同一空间内的成员不再互相看见对方的资源，
工作区也不再漏出平台自身的运行时目录。

### 新增

- **资源级用户隔离**：`owner_id` 贯穿 `conversations` / `agent_runs` / `knowledge_bases` / `experts` / `skills` / `mcp_servers` / `api_keys` 七张表（迁移 009，幂等加列 + 复合索引）。语义为「预置共享 + 用户私有」——`''` 表示系统预置（全员可见、仅管理员可改），非空表示创建者（仅本人可见，管理员亦不越权，与个人空间边界一致）
  - 双维度收敛 `owned_kb_ids(ws, ids, uid)` / `visible(id, ws, uid)`：workspace 防跨空间、owner 防同空间越权
  - 可读与可改分离（`can_edit(item, uid, is_admin)`）；越权探测统一 404 不泄露存在性，预置资源改不动返回 403
  - 技能 id 混入归属（`_skill_id(ws, repo, subpath, owner_id)`），同空间两人装同一 GitHub 技能不再互相覆盖；工作区技能目录只发布「预置 + 本人」
  - API Key 继承签发者范围，开放 API 按 `key.owner_id` 收敛知识库与专家，不会因管理员签发而获得全库通行
  - 对话续写收到他人 `conversation_id` 时静默新开会话；MCP 工具按 `visible_server` 收敛
- **项目先登记再创建**：新增 `projects` 表（迁移 010，`workspace_id + slug` 复合主键，含 `owner_id` / `internal`）。列表只认「已登记 + 属于当前用户」的行，磁盘目录降级为落点；项目没有「预置」档，每个项目都属于某个人，空间管理员也不越权，越权一律 404
- **运行时目录迁出用户命名空间**：技能同步目录 `skills/` → `.agent/skills/`，临时沙盒 `_scratch` → 按用户隔离的 `_scratch-<uid>`；模型侧根目录 `ls` 只显示活动项目，`/.agent/skills/` 仍可读（渐进披露不变），写 `.agent/**` 一律拒绝。文件树与整包下载统一跳过 `.agent` / `.deck` / `.deckpreview` / `.slidep`
- **租户数据一键清理**：`tools/reset_tenant_data.py`（`--dry-run` / `--yes`），删前整库 `pg_dump` + 文件层 tar 备份；清 17 张业务表与非 admin 账号/空间，保留预置技能与专家
- **验收脚本**：`tools/verify_isolation.py`（两账号交叉验证六类资源 28 项）、`tools/verify_projects_scope.py`（项目可见性 22 项）

### 修复

- 系统管理页「新增成员」按钮被说明文字挤到 60px 导致四字折行溢出：文字块 `min-w-0 flex-1`、按钮 `shrink-0 whitespace-nowrap`，成员表改通栏展示
- 迁移前存量资源 `owner_id` 为空等于全员共享，清理后统一回填为创建者
- 删除账号时未连带清理其个人空间，留下孤儿空间与成员关系
- `verify_agent` 在 deep 引擎下的旧工具名断言（`fs_write` 已被 harness 工具取代）
- `verify_rbac_http` 清理逻辑遗漏探测用户的个人空间，残留测试数据

### 变更

- `skills`、`_` 前缀列为项目保留名，创建直接 400
- 启动自愈 `projects.adopt_orphans()`：把升级前的存量目录收养登记给空间所有者（宁缺勿泄），跳过运行时目录

## [0.3.0] - 2026-11-09

对应第二阶段批次 S2.3 / S2.4 / S2.5 / S2.6，RAG 质量平台化体验生态收官。

### 新增

- **检索质量（S2.3）**：查询改写（LLM 改写 + 超时回退 + 缓存）；Rerank 三级提供方（none / OpenAI 兼容 http / ONNX，失败自动降级原序）；父子切片（大父片拼接回原文，`parent_expand` 开关）；引用原文定位（字符区间 + 前后 200 字摘录，前端点引用展开）；评测裁判指标 context_precision / faithfulness；评测运行历史与 Δ 对比；检索多模式对比测试台
- **平台化（S2.4）**：viewer 只读角色（写路径中间件拦截）；令牌桶限流（登录/对话/智能体/检索/开放 API 分级，Redis 固定窗口 + 内存兜底）；工作区日配额（请求数 / token）；邀请码加入工作区；审计筛选与 CSV 导出；shared 可见性跨工作区共享知识库
- **对话与智能体体验（S2.5）**：会话重命名 / 置顶 / 归档 / 全文搜索（标题+正文）；回答赞踩反馈；长期记忆（手动管理 + 相关记忆自动注入系统提示）；模型降级链（`base_url::model::api_key;...` 依次切换）；敏感工具人工确认（暂停 → 批准条 → 自动续跑）
- **技能/专家/MCP 完善（S2.6）**：技能 semver 版本管理与一键回滚；技能 zip 导入导出（防炸弹 + 路径穿越防护）；技能沙箱试运行；专家开场白 / 推荐问题 / 发布流（draft→testing→live，live 才进对话页）；MCP 凭证加密落盘（SHA-256 密钥流，掩码回显）；MCP 工具审批白名单；MCP 调用并发隔离（单服务器上限 4）与审计留痕

### 修复

- 会话全文搜索 SQL 括号未闭合导致 500（`EXISTS(...)` 外层括号缺失）
- PG 读路径失败不回滚，事务停留在 aborted 状态毒化后续所有请求
- 技能导出文件名含中文导致 latin-1 header 编码 500（改用 RFC 5987 filename*）
- 会话 PATCH 重命名与置顶/归档互斥，无法同时生效

## [0.2.0] - 2026-11-09

对应第二阶段批次 S2.0 / S2.1 / S2.2 / S2.7 及智能体引擎升级。

### 新增

- **存储层生产组合**：PostgreSQL(5433) / Redis(6380) / Milvus(19530) 真实接线，默认启用；保留 json/sqlite/memory 后端可切换（`APP_STORE_BACKEND` / `APP_VECTOR_BACKEND` / `APP_QUEUE_BACKEND`），Repository 双后端同契约（100 项静态对齐检查）
- **RAG 写路径规模化**：PDF / Word / Excel / PPT / CSV / HTML / Markdown 多格式解析；异步摄取任务（解析→去重→切片→嵌入→入库，进度可查）；sha256 文档去重；同文档多版本与回滚；切片预览与编辑（单条重嵌入）
- **整目录导入**：前端选文件夹 / 拖拽目录（保留相对路径作文档名）/ ZIP 自动解包 / 服务端目录白名单导入；单批文件数与大小默认不限（`APP_IMPORT_BATCH_MAX_FILES/MB=0`），按 `APP_IMPORT_BATCH_SHARD`（默认 50）分片并行消费
- **智能体引擎升级**：默认切换 Deep Agents harness（`deepagents 0.7.34`）——显式计划（write_todos→前端计划卡）、上下文摘要压缩、task 子智能体；文件落盘走 FilesystemBackend（根=工作区），产物卡片/预览/下载不变；`APP_AGENT_ENGINE=react` 可回滚旧引擎
- **KB 优先检索**：挂载知识库时首轮强制预取检索注入上下文 + 系统指令硬规则（KB 第一信息源、查不到才 web_search、注明文档名）
- **交付工程**：Dockerfile（server 多阶段 / web+nginx）、`docker-compose.prod.yml`、GitHub Actions CI（ruff/tsc/契约检查/API 文档防漂移）、`docs/API参考.md`（95 操作，OpenAPI 生成）、Python SDK（chat/search/agent/kb）、备份脚本 `backup.py` / `restore.py`

### 修复

- worker 空闲时不再静默退出（redis-py ≥6 的 BLPOP 超时异常）；槽线程异常兜底，防"槽静默消失→进程 exit 0"
- 在途任务不再往已删除的知识库写数据（消费前校验，孤儿文档/切片清零）
- 批量暂存目录按 TTL 兜底清理（`APP_IMPORT_STAGING_TTL_HOURS`，默认 6h）+ 启动回收；handler 只删自己的文件并如实上报删不掉的
- 切片 id 撞号（删文档后新切片与旧切片 id 重复导致检索串内容）
- 检索热路径：BM25 分词按 chunk_id 缓存；`list_chunks(with_vector=False)` 不再把 4096 维向量读进内存——hybrid p50 1196ms→99ms，lexical 1257ms→57ms
- 路径安全：zip 穿越防护、符号链接拒绝、服务端目录白名单 resolve 比较

### 已知问题

- 项目名含非法字符时被静默改名而非拒绝（P2）
- 引用溯源止于文档名+切片文本，未定位到原文段落（S2.3）
- 单租户：无多租户隔离、限流与配额（S2.4）；初始账号 `admin/admin123`，**部署后必须立即改密码**，公网部署前建议自行加登录限流

### 升级说明

从 v0.1.0 升级：`server/tools/migrate_json_to_db.py` 幂等迁移 JSON 数据到 PostgreSQL；`rebuild_vector_index.py` 回灌 Milvus；详见 `docs/运维手册.md`。

## [0.1.0] - 2026-11-05

- RAG 问答闭环：摄取管道 → 混合检索（向量+BM25+RRF）→ 流式回答 + 引用卡片
- 智能体：LangGraph ReAct、内置工具 + MCP 工具注册表、步骤流可视化、多轮会话
- 技能 / 专家 / MCP 管理、项目沙盒（文件树、预览、PPT 12 版式）
- 平台：工作区、API Key、审计、指标、评测集、开放 API（`/v1/chat/completions` 等）
