# 云沙箱模板：三套运行时（Python / Node / Java + Maven）

> **现在不必需**。实测阿里云官方 `code-interpreter-v1` 已经带了 JDK 21，只缺
> Maven；执行器会在第一次 `maven` 调用时把官方 Maven 二进制装进会话（约 10MB，
> 实测 1 秒内），配置项见 `.env.example` 的 `E2B_MAVEN_URL`。本文档保留给两种情况：
> ① 要求上线即预热、不想让首个用户等那 1 秒；② 出口白名单只放行少数域名，不允许
> 运行时下载 Maven。

官方 `base` 与 `code-interpreter-v1` 早先只带 Python 和 Node。模型写 Java 时会在
沙箱里拿到 `java: command not found`，看起来像「模型不会写 Java」，实际是模板
里没有运行时。这个模板把三套装齐，让云端与本地容器能力对齐。

## 一、先确认账号侧就绪

1. 开通**函数计算**与**云沙箱**；
2. 在云沙箱控制台创建 Team，记下 **Team ID**（走 OpenAPI 建模板要用）；
3. 拿三个连接参数（地域必须一致，示例为北京）：

```bash
E2B_API_KEY=<控制台的 API Key>
E2B_API_URL=https://api.cn-beijing.sandbox.aliyuncs.com
E2B_DOMAIN=cn-beijing.sandbox.aliyuncs.com
```

写进 `server/.env`：

```ini
APP_AGENT_CODE_EXEC_SANDBOX=e2b
APP_AGENT_CODE_EXEC_E2B_API_KEY=...
APP_AGENT_CODE_EXEC_E2B_API_URL=...
APP_AGENT_CODE_EXEC_E2B_DOMAIN=...
APP_AGENT_CODE_EXEC_E2B_TEMPLATE=einstein-langs
```

## 二、构建模板

方式 A：E2B CLI（首次验证用，最快）

```bash
pip install "e2b==2.31.0"
e2b template init --name einstein-langs     # 把本目录的 Dockerfile 纳入
e2b template build -n einstein-langs
e2b template list                           # 看到 einstein-langs 且 READY 即可
```

方式 B：OpenAPI（CI 用）

```bash
pip install alibabacloud_sandbox20260820
# CreateTemplate 一次完成「创建 + 首次构建」，构建是异步的，
# 轮询 GetTemplate 直到 status=READY
```

RAM 身份需要这四条权限：

```json
{"Version":"1","Statement":[{"Effect":"Allow","Action":[
  "fcsandbox:CreateTemplate","fcsandbox:GetTemplate",
  "fcsandbox:ListTemplates","fcsandbox:DeleteTemplate"],"Resource":"*"}]}
```

## 三、验证

```bash
cd server
.venv/Scripts/python.exe tools/verify_sandbox_langs.py --cloud
```

期望：python / node / java 三项 `[ok]`，`mvn test` 通过，`云沙箱可出网` + `pip 可装包`
两条 `[ok]`。若 Java 仍是 `[--] 跳过`，说明模板还没生效——检查
`APP_AGENT_CODE_EXEC_E2B_TEMPLATE` 是否指向新模板名。

## 四、已知边界

- **基础镜像必须是 `/sbin/init` 起得来**：第二代运行时要 systemd 常驻，
  少了 `systemd-sysv` 会起来就退。
- **Maven 不用 apt 的包**：Debian 的 maven 把 surefire 钉在 2.17，不认 JUnit 5，
  表现为「测试莫名其妙全失败」。用官方 3.9.9 二进制。
- **首构建约 3~5 分钟**：全程拉 Debian 源 + npm 全局包。构建失败先看
  是不是 `debian:13` 的源地址不可达。
- **模板更新不会自动作用到已存在的沙箱**：改完模板要重启后端（清掉会话缓存），
  或等 `E2B_IDLE` 到点回收。

> 本机 Docker Desktop 当前未运行，这份 Dockerfile **尚未在本机构建验证**；
> 上面的步骤在 Docker 可用后按序执行即可。
