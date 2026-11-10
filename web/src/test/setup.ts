import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// 显式卸载每个用例挂载的树。RTL 的自动 cleanup 依赖全局 afterEach 存在
// （globals: true）且该模块的顶层副作用被执行到；一旦哪个用例文件先
// import 了自己的 afterEach，这份自动注册就可能不生效，表现为上一个
// 用例的 DOM 留在 body 里，后面的 getByRole 全变成 "Found multiple
// elements"。自己注册一次成本为零，且与其他用例文件的写法无关。
afterEach(() => {
  cleanup();
});

// 说明：这里不全局静音 console.error。错误边界一类的用例会**故意**抛错，
// React 会把组件栈打到 console.error——在用例内部用 vi.spyOn(console, "error")
// 局部关掉并在断言后还原，比在 setup 里全局静音更能暴露意外的报错路径。

// jsdom 没有实现 innerText，而幻灯片画布的就地编辑完全依赖它读写内容。
// 用 textContent 顶替：两者在「纯文本内容」上等价，这正是那些用例的覆盖面。
if (!("innerText" in HTMLElement.prototype)) {
  Object.defineProperty(HTMLElement.prototype, "innerText", {
    configurable: true,
    get(this: HTMLElement) {
      return this.textContent ?? "";
    },
    set(this: HTMLElement, value: string) {
      this.textContent = value;
    },
  });
}
