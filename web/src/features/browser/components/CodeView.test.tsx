import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CodeView, langOfPath } from "./CodeView";

/**
 * 代码预览的两个容易退化的点，各锁一条：
 * 1. 后缀 → 语言的映射。漏一个后缀不会报错，只是那个文件永远不上色，
 *    肉眼看代码仍然「能读」，所以必须靠断言盯住。
 * 2. 行号与代码行数一一对应。行号算错（多一行/少一行）整列都会错位。
 */

describe("langOfPath", () => {
  it("按后缀识别常见语言", () => {
    expect(langOfPath("app/main.py")).toBe("python");
    expect(langOfPath("src/index.tsx")).toBe("typescript");
    expect(langOfPath("src/util.js")).toBe("javascript");
    expect(langOfPath("pom.xml")).toBe("xml");
    expect(langOfPath("docs/index.html")).toBe("xml");
    expect(langOfPath("styles/theme.scss")).toBe("scss");
    expect(langOfPath("config/app.yaml")).toBe("yaml");
    expect(langOfPath("scripts/build.sh")).toBe("bash");
    expect(langOfPath("native/core.cpp")).toBe("cpp");
  });

  it("无后缀靠文件名识别，纯文本返回 null", () => {
    expect(langOfPath("Dockerfile")).toBe("dockerfile");
    expect(langOfPath("Makefile")).toBe("makefile");
    expect(langOfPath(".gitignore")).toBe("bash");
    expect(langOfPath("notes.txt")).toBeNull();
    expect(langOfPath("data.csv")).toBeNull();
    expect(langOfPath("noext")).toBeNull();
  });

  it("大小写不敏感", () => {
    expect(langOfPath("README.MD")).toBe("markdown");
    expect(langOfPath("APP.PY")).toBe("python");
  });
});

describe("CodeView", () => {
  it("行号与代码行数一致", () => {
    const { container } = render(<CodeView code={"a = 1\nb = 2\nc = 3"} language="python" />);
    const gutter = container.querySelector(".code-gutter") as HTMLElement;
    expect(gutter.children).toHaveLength(3);
    expect(gutter.textContent).toBe("123");
  });

  it("按语言产出高亮 span，而不是原样一段文本", () => {
    const { container } = render(
      <CodeView code={'def hi():\n    return "x"'} language="python" />,
    );
    expect(container.querySelector(".hljs-keyword")).toBeTruthy();
    expect(container.querySelector(".hljs-string")).toBeTruthy();
  });

  it("语言未知时退化成纯文本，仍然显示行号", () => {
    const { container } = render(<CodeView code={"hello\nworld"} language={null} />);
    expect(container.querySelector(".hljs-keyword")).toBeNull();
    const gutter = container.querySelector(".code-gutter") as HTMLElement;
    expect(gutter.children).toHaveLength(2);
    expect(container.textContent).toContain("hello");
  });

  it("HTML 里的尖括号按文本转义，不生成真实节点", () => {
    const { container } = render(
      <CodeView code={'<script>alert("x")</script>'} language="xml" />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
  });
});
