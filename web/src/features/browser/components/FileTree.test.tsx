import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FileTree } from "./FileTree";
import type { TreeEntry } from "../types";

/**
 * 产物接口只回文件、不回目录（产物清单天然是文件集合），所以树必须由路径
 * 现造。这里锁三件事：目录节点真的出现、单链目录被压成一行、折叠能收起子项。
 * 之前退化成缩进列表时，40 多个 .java 平铺成一条，看不出模块结构。
 */

const files: TreeEntry[] = [
  { path: "pom.xml", type: "file", size: "1.1 KB" },
  { path: "README.md", type: "file", size: "144 B" },
  {
    path: "ecommerce-common/src/main/java/com/shop/common/api/Result.java",
    type: "file",
    size: "800 B",
  },
  {
    path: "ecommerce-common/src/main/java/com/shop/common/api/PageResult.java",
    type: "file",
    size: "900 B",
  },
  { path: "ecommerce-common/pom.xml", type: "file", size: "2.0 KB" },
  { path: "ecommerce-user/pom.xml", type: "file", size: "2.0 KB" },
];

const noop = () => {};

describe("FileTree", () => {
  it("从文件路径造出目录节点，而不是平铺", () => {
    render(<FileTree entries={files} active="" onPick={noop} />);
    // 顶层目录
    expect(screen.getByText("ecommerce-common")).toBeTruthy();
    expect(screen.getByText("ecommerce-user")).toBeTruthy();
    // 深层的单链目录被压成一行，省掉四次点击
    expect(screen.getByText("src.main.java.com.shop.common.api")).toBeTruthy();
    // 文件本身带名字
    expect(screen.getByText("Result.java")).toBeTruthy();
  });

  it("折叠目录后子项消失，再点一次回来", async () => {
    const user = userEvent.setup();
    render(<FileTree entries={files} active="" onPick={noop} />);

    expect(screen.getByText("Result.java")).toBeTruthy();
    await user.click(screen.getByText("ecommerce-common"));
    expect(screen.queryByText("Result.java")).toBeNull();
    // 兄弟目录不受影响
    expect(screen.getByText("ecommerce-user")).toBeTruthy();

    await user.click(screen.getByText("ecommerce-common"));
    expect(screen.getByText("Result.java")).toBeTruthy();
  });

  it("点文件回传完整相对路径", async () => {
    const user = userEvent.setup();
    let picked = "";
    render(<FileTree entries={files} active="" onPick={(next) => (picked = next)} />);
    // 三个模块都有 pom.xml，靠 title（完整路径）区分，取根目录那个
    const rootPom = screen
      .getAllByText("pom.xml")
      .find((node) => node.parentElement?.getAttribute("title") === "pom.xml");
    expect(rootPom).toBeTruthy();
    await user.click(rootPom!);
    expect(picked).toBe("pom.xml");
    await user.click(screen.getByText("Result.java"));
    expect(picked).toBe("ecommerce-common/src/main/java/com/shop/common/api/Result.java");
  });

  it("后端若同时给了目录条目，不会重复建节点", () => {
    const withDirs: TreeEntry[] = [
      { path: "docs", type: "dir" },
      { path: "docs/guide.md", type: "file", size: "1 KB" },
    ];
    render(<FileTree entries={withDirs} active="" onPick={noop} />);
    expect(screen.getAllByText("docs")).toHaveLength(1);
    expect(screen.getByText("guide.md")).toBeTruthy();
  });
});
