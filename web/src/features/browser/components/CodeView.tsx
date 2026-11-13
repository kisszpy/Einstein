/**
 * 代码只读视图：行号沟 + 语法高亮。
 *
 * 为什么不用现成的「编辑器组件」：产物预览是只读场景，Monaco/CodeMirror 带进来的
 * 是整套编辑态（光标、选区、撤销栈、worker），体积与初始化成本都不值当。
 * 这里只做两件事——把代码切词上色、把行号对齐。
 *
 * 高亮走 highlight.js 的 core 按需注册：默认包带 190+ 语言，全量引入会白白塞进
 * 几百 KB 用不到的语法定义。这里只注册产物里真会出现的那些。
 *
 * 行号不用 highlight.js 的 line-numbers 插件：那个插件把 HTML 再拆一遍，
 * 会破坏跨行的字符串/注释。改用「等高双列」——沟与代码同一 line-height，
 * 代码不换行（white-space: pre），行号天然一一对应，横向滚动时沟用 sticky 钉住。
 */

import { useMemo } from "react";
import hljs from "highlight.js/lib/core";

import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import dart from "highlight.js/lib/languages/dart";
import diff from "highlight.js/lib/languages/diff";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import dos from "highlight.js/lib/languages/dos";
import go from "highlight.js/lib/languages/go";
import graphql from "highlight.js/lib/languages/graphql";
import ini from "highlight.js/lib/languages/ini";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import less from "highlight.js/lib/languages/less";
import lua from "highlight.js/lib/languages/lua";
import makefile from "highlight.js/lib/languages/makefile";
import markdown from "highlight.js/lib/languages/markdown";
import nginx from "highlight.js/lib/languages/nginx";
import perl from "highlight.js/lib/languages/perl";
import php from "highlight.js/lib/languages/php";
import plaintext from "highlight.js/lib/languages/plaintext";
import powershell from "highlight.js/lib/languages/powershell";
import properties from "highlight.js/lib/languages/properties";
import protobuf from "highlight.js/lib/languages/protobuf";
import python from "highlight.js/lib/languages/python";
import r from "highlight.js/lib/languages/r";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import scala from "highlight.js/lib/languages/scala";
import scss from "highlight.js/lib/languages/scss";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";

/** 注册表：id → 语法定义。key 同时是 hljs 的语言名与 EXT_LANG 的取值。 */
const LANGUAGES = {
  bash,
  c,
  cpp,
  csharp,
  css,
  dart,
  diff,
  dockerfile,
  dos,
  go,
  graphql,
  ini,
  java,
  javascript,
  json,
  kotlin,
  less,
  lua,
  makefile,
  markdown,
  nginx,
  perl,
  php,
  plaintext,
  powershell,
  properties,
  protobuf,
  python,
  r,
  ruby,
  rust,
  scala,
  scss,
  sql,
  swift,
  typescript,
  xml,
  yaml,
} as const;

Object.entries(LANGUAGES).forEach(([id, def]) => hljs.registerLanguage(id, def));

/** 后缀 → hljs 语言。找不到就返回 null：宁可不上色，也别瞎猜错语法。 */
const EXT_LANG: Record<string, keyof typeof LANGUAGES> = {
  py: "python",
  pyi: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  geo: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  cfg: "ini",
  conf: "ini",
  properties: "properties",
  env: "bash",
  md: "markdown",
  markdown: "markdown",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  bat: "dos",
  cmd: "dos",
  ps1: "powershell",
  sql: "sql",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  hpp: "cpp",
  hh: "cpp",
  java: "java",
  kt: "kotlin",
  kts: "kotlin",
  go: "go",
  rs: "rust",
  rb: "ruby",
  php: "php",
  swift: "swift",
  lua: "lua",
  r: "r",
  pl: "perl",
  diff: "diff",
  patch: "diff",
  makefile: "makefile",
  mk: "makefile",
  dockerfile: "dockerfile",
  graphql: "graphql",
  gql: "graphql",
  proto: "protobuf",
  dart: "dart",
  scala: "scala",
  scss: "scss",
  less: "less",
  css: "css",
  xml: "xml",
  html: "xml",
  htm: "xml",
  vue: "xml",
  svg: "xml",
};

/** 无后缀但靠文件名就能定语言的（Dockerfile / Makefile / .gitignore）。 */
const NAME_LANG: Record<string, keyof typeof LANGUAGES> = {
  dockerfile: "dockerfile",
  makefile: "makefile",
  ".gitignore": "bash",
  ".env": "bash",
};

/** 从路径推断 hljs 语言名；纯文本（txt/log/csv…）返回 null。 */
export function langOfPath(path: string): string | null {
  const name = (path.split("/").pop() ?? "").toLowerCase();
  if (!name) return null;
  const byName = NAME_LANG[name];
  if (byName) return byName;
  const dot = name.lastIndexOf(".");
  if (dot < 0) return null;
  return EXT_LANG[name.slice(dot + 1)] ?? null;
}

interface Props {
  code: string;
  /** hljs 语言名；null / 未注册时退化成不着的纯文本（行号照给） */
  language?: string | null;
}

export function CodeView({ code, language = null }: Props) {
  const html = useMemo(() => {
    if (!language || !hljs.getLanguage(language)) return null;
    try {
      // ignoreIllegals：产物里的代码常有半截片段（多语言混写、模板串被截断），
      // 让它照常上色比抛错中断体验好
      return hljs.highlight(code, { language, ignoreIllegals: true }).value;
    } catch {
      return null;
    }
  }, [code, language]);

  const lineCount = useMemo(() => code.split("\n").length, [code]);
  const numbers = useMemo(
    () => Array.from({ length: lineCount }, (_, i) => i + 1),
    [lineCount],
  );

  return (
    <div className="code-view h-full overflow-auto bg-surface-1 font-mono text-[12.5px] leading-[1.62]">
      <div className="flex w-max min-w-full items-stretch">
        <div
          aria-hidden
          className="code-gutter sticky left-0 z-10 shrink-0 select-none border-r border-line bg-surface-1 px-2.5 py-3 text-right text-tertiary"
        >
          {numbers.map((n) => (
            <div key={n}>{n}</div>
          ))}
        </div>
        <pre className="m-0 min-w-0 flex-1 px-3 py-3 [tab-size:2]">
          {html === null ? (
            <code className="hljs">{code}</code>
          ) : (
            // highlight.js 自己转义过 <>&，输出只有 span + 文本，不存在注入面
            <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
          )}
        </pre>
      </div>
    </div>
  );
}
