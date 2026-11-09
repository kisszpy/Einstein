/**
 * 正文里的引用编号 → Unicode 上标。
 *
 * 背景：联网回答的提示词要求「引用处句末标注 [W1]」，但模型经常写成
 * 【w1/w2/w4】——小模型对输出格式的遵循度就这样，改提示词只能降低概率，
 * 消不掉。而正文里裸露一排 `【w1/w2/w4】` 确实碍读，所以在渲染这一层兜住。
 *
 * 为什么是上标而不是直接删掉：下方的「已联网 · N 个来源」面板按编号列出来，
 * 上标让「这句话依据第几条」还能对得上；删掉就只剩一坨无出处的断言。
 *
 * 为什么用 Unicode 上标（¹²³）而不是 `<sup>`：不必引 rehype-raw，也就不给
 * HTML 直通开一个口子；流式输出逐帧处理也没有额外开销。
 *
 * 代码块里的内容一律不动——用户问代码时，数组字面量 `[1,2]` 不能被改成上标。
 */

const SUPERSCRIPT: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
  "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
};

/**
 * 【w1】/【W1/W2】/【1/2】—— 全角括号一定是模型的说法，纯数字也认。
 * 编号限 1~2 位：`【2026】` 这种年份不能被当成第 2026 条来源。
 */
const FULLWIDTH = /【\s*(?:[wW]\s*)?(\d{1,2}(?!\d)(?:\s*[/、,，]\s*(?:[wW]\s*)?\d{1,2}(?!\d))*)\s*】/g;
/**
 * [W1] —— 半角必须带 w/W 前缀。
 *
 * 为什么不认裸 `[1]`：正文里的 `[1]` 可能是数组 `[1,2]`、年份 `[2026]`，
 * 也可能是 Markdown 链接的一部分，靠正则分不干净。而知识库引用的 `[1]`
 * 本身是学术脚注写法，与下方「引用来源」面板的编号一一对应，留着有用。
 * 末尾 `(?!\()` 是链接护栏：`[W1](url)` 那种不能碰。
 */
const HALFWIDTH = /\[\s*[wW]\s*(\d{1,2}(?!\d)(?:\s*[/、,，]\s*(?:[wW]\s*)?\d{1,2}(?!\d))*)\s*\](?!\()/g;

/**
 * 要不要把编号显示成上标。
 *
 * `true`  → `价格已上涨【w1/w2/w4】` 变成 `价格已上涨¹,²,⁴`
 * `false` → 编号整段删掉，正文最干净，代价是看不出这句依据哪条来源
 *
 * 放在代码里而不是配置里，是因为这是纯展示取舍：做成配置项得为它加一个设置
 * 界面，而真正会去改的人就是维护者本人。
 */
const SHOW_MARKS = true;

function toSuperscript(marks: string): string {
  const seen = new Set<string>();
  const nums: string[] = [];
  for (const match of marks.matchAll(/\d+/g)) {
    if (!seen.has(match[0])) {
      seen.add(match[0]);
      nums.push(match[0]);
    }
  }
  if (nums.length === 0) return "";
  // 多个编号之间留逗号：¹²⁴ 会被读成「一百二十四」
  return nums
    .map((n) => [...n].map((d) => SUPERSCRIPT[d] ?? d).join(""))
    .join(",");
}

function replaceMarks(text: string): string {
  const render = (marks: string) => (SHOW_MARKS ? toSuperscript(marks) : "");
  return text
    .replace(FULLWIDTH, (_all, marks: string) => render(marks))
    .replace(HALFWIDTH, (_all, marks: string) => render(marks));
}

export function normalizeCitations(content: string): string {
  // 没有方括号就不必拆段，省掉流式输出时每帧的正则开销
  if (!content || !/[[【]/.test(content)) return content;
  // 按 ``` 围栏切开，奇数段是代码块，原样保留
  const segments = content.split(/(```[\s\S]*?```)/);
  return segments.map((seg, i) => (i % 2 === 1 ? seg : replaceMarks(seg))).join("");
}
