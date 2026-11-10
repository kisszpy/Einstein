# Fork 说明：给 Web 构建补上中文

本目录是 [storytold/deckcraft](https://github.com/storytold/deckcraft) v0.16.0 的 fork，唯一目的：
**让 wasm 版本能正常显示中文**。

上游的定位是英文产品，Web 构建按「体积预算」只嵌 Inter 和 BIZ UDPGothic（日文），简体中文清单
只在 native 构建读。结果是浏览器里每一个汉字都画成方框 `.notdef`——实测两份中文 deck 全部如此，
对中文产品是一票否决。

## 改了什么（4 处，都可独立回退）

| 文件 | 改动 |
|---|---|
| `crates/fonts/build.rs` | 新增 `WEB_CJK_FAMILY` 常量与 `web_face_allowed()`，让中文字体穿过 wasm 体积过滤器 |
| `crates/fonts/build.rs` | `craft_fonts()` 在**所有目标**上读 `fonts/cjk-manifest.txt`（上游的中文清单仍只在 native 读，不动） |
| `crates/fonts/src/fontdb.rs` | 新增 `is_cjk_family()` / `CJK_FALLBACK_FAMILY`；`substitutes()` 加中文分支 |
| `crates/fonts/src/fontdb.rs` | `document_face()`：未安装的中文字体走内置汉字面，而不是落到 Calibri |

### 为什么换家族名，而不是放开排除规则

上游 `is_excluded_family()` 明确排除 `Noto Sans CJK*`，并且**有测试断言这一点**
（`all_noto_cjk_families_are_excluded_from_every_platform`）。直接改排除表会让上游测试变红，
以后每次 rebase 都要处理冲突。

所以子集字体被重命名为 **`DeckCraft Han Sans`**：排除规则和它的测试一行不动，`git merge upstream`
干净。OFL-1.1 允许修改与再分发；改名同时也表明这是「修改版」，不冒用 Noto 的名。

### 字体来源与体积

Noto Sans CJK SC Regular/Bold（craft-fonts 已 pin 的 commit `f8d1575`，OFL-1.1），用 fontTools
子集化到 **24,671 个码位**：GBK 全集 21,003 字（GB18030 双字节区——中文输入法能打出来的
边界，通用规范汉字表 8,105 字是其子集）+ Latin-1 + 通用标点 + 箭头 + 带圈数字 +
几何图形 + CJK 标点 + 全角形式。

| | 大小 |
|---|---|
| 原始 OTF（单字重） | 16.4 MB |
| 子集（单字重） | **8.94 MB** |
| 两字重增量 | **17.95 MB** |
| wasm 总量 | 32 MB → 52 MB（gzip 11.2 → 28 MB） |

字符集口径的两次调整：最初 GB2312（8,209 码位，2.87 MB/字重），生僻人名用字
（龘、珺、玥）落空成方框；2026-11-11 扩到 GBK 全集。CJK 扩展 A/B（𬭚 等）仍不在
GBK 内，是 `.notdef`——要再扩就改 `tools/build-han-subset.py` 的 `code_points()`
重跑，约 0.35 KB/字/字重。

## 重新构建

```bash
# 1) 准备 craft-fonts（约 100 MB，含上游拉丁/日文字体）
#    放到 tools/craft-fonts，或任意位置后用 CRAFT_FONTS_DIR 指过去
git clone https://github.com/storytold/craft-fonts tools/craft-fonts

# 2) 生成中文子集（会改写 craft-fonts/fonts/han-sans-sc/，并打印 sha256）
python tools/build-han-subset.py --craft-fonts tools/craft-fonts

# 3) 把 sha256 填回 craft-fonts/fonts/cjk-manifest.txt，然后构建
cd apps/deckcraft-web
CRAFT_FONTS_DIR=/abs/path/to/tools/craft-fonts trunk build --release
# 产物在 ../../dist/web
```

需要 Rust 1.95+（`rustup target add wasm32-unknown-unknown`）、trunk 0.22、Python 3 +
`fonttools`。trunk 会自动下载匹配版本的 wasm-bindgen 和 wasm-opt。

构建后校验嵌入的字体（这一步最容易静默失败——过滤器是 `continue`，不报错）：

```bash
grep -o 'family: "[^"]*"' target/wasm32-unknown-unknown/release/build/deckcraft-fonts-*/out/craft_fonts.rs \
  | sort | uniq -c
# 期望看到 DeckCraft Han Sans ×2
```

## 合并上游的注意点

- `build.rs` 的三处改动都在独立函数/独立块里，冲突概率低。
- `fontdb.rs` 的两个分支都带 `// Fork addition:` 注释，`grep "Fork addition"` 可一次找全。
- 上游若将来自己支持 wasm 中文（官方路线图把简中列为 Beta 必须项，估 30–45h 本地化工程），
  优先丢掉本 fork 的字体部分，改用上游实现。
- `fonts/cjk-manifest.txt` 放在 craft-fonts 仓库里而非本仓库，因为 build.rs 的相对路径解析在那边；
  该文件与 `fonts/han-sans-sc/` 一起构成 fork 的字体资产。

## 已知未覆盖

- 只支持 Regular/Bold（上游渲染侧本来也只认这三档，Medium/Semibold 会被归并）
- 没有 CJK 衬线面：宋体/楷体/仿宋请求统一用 `DeckCraft Han Sans` 画（有字形总比方框好），
  这一点在 `fontdb.rs` 的注释里写明了
- pptx 里的动画/切换仍不解析（与字体无关的既有缺口）
