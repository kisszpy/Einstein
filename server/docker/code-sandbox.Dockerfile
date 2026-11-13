# 智能体代码执行沙箱镜像（Python + Node + JVM）。
#
# 为什么需要自建：`code_run` 跑在容器里（见 app/services/capabilities/code_exec.py），
# 隔离换来的代价是纯净镜像什么都没有——模型处理 Excel 会 ModuleNotFoundError，
# 写 Java/Node 交付物会 command not found，而容器是 `--network none`，
# 它连不上网自己装。所以工具链必须烘进镜像。
#
# 为什么用 apt 而不是官方 node/maven 镜像做多阶段 COPY：构建机在受限网络里，
# 拉 node:22-slim / maven:3.9 这种含大层的镜像会卡在 registry 上（实测十分钟
# 不动），而 Debian 源稳定可达。python:3.12-slim 现在基于 Debian 13（trixie），
# 官方源里就带 Node 20.19 / Maven 3.9.9 / OpenJDK 21——版本正好是各条线的
# LTS，模型对这几个版本的语法最熟，不用退而求其次。
#
# 构建：
#   docker build -t einstein-code-sandbox:latest -f docker/code-sandbox.Dockerfile docker
# 启用（.env）：
#   APP_AGENT_CODE_EXEC_IMAGE=einstein-code-sandbox:latest
#
# 容器运行时由引擎负责加隔离参数（--network none --read-only --cap-drop ALL
# --tmpfs /tmp -v <项目>:/work），镜像本身不需要做任何安全设置。
# syntax=docker/dockerfile:1

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    LANG=C.UTF-8 \
    TZ=Asia/Shanghai

# 一次装齐三套运行时。headless 版 JDK 没有 X11/AWT 依赖，省 80MB——
# 沙箱里不会画 Swing 界面（matplotlib 走 Agg）。
#
# 注意这里**不装 apt 的 maven**：Debian 把 default-bindings 里的
# maven-surefire-plugin 钉在 2.17（2014 年的版本），而 2.17 根本不支持
# JUnit 5——模型写带测试的 Java 项目，`mvn test` 会报「no tests to run」
# 或者干脆找不到 provider，看起来像它写错了测试。官方 3.9.9 的默认绑定是
# surefire 3.2.5 / jar 3.4.1，正好是下面预热的那批，两边对齐。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      tzdata \
      openjdk-21-jdk-headless \
      nodejs \
      npm \
 && rm -rf /var/lib/apt/lists/* \
      /usr/share/doc/* \
      /usr/share/man/* \
      /usr/lib/jvm/*/lib/src.zip \
      /usr/lib/jvm/*/jmods

# 官方 Maven 二进制（9MB，走 Maven 仓库本身，构建机可达）。用 python 下载
# 而不是 curl/wget：基础镜像里本来就有 python，少装一个包。
RUN python - <<'PY'
import io, os, tarfile, urllib.request

url = ("https://maven.aliyun.com/repository/public/org/apache/maven/"
       "apache-maven/3.9.9/apache-maven-3.9.9-bin.tar.gz")
blob = urllib.request.urlopen(url, timeout=180).read()
tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz").extractall("/opt")
os.rename("/opt/apache-maven-3.9.9", "/opt/maven")
PY

# JAVA_HOME 用软链而不是写死 java-21-openjdk-amd64：换 arm64 构建时
# 目录名会变，写死的话 mvn 直接找不到 JDK
RUN ln -sfn "$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")" /opt/java-home
ENV JAVA_HOME=/opt/java-home
ENV PATH=/opt/maven/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/sbin:/bin

RUN pip install --no-cache-dir \
    pandas \
    numpy \
    openpyxl \
    python-docx \
    python-pptx \
    matplotlib \
    tabulate

# tsc/tsx 预装成全局命令：模型写 TypeScript 时常直接 `tsx foo.ts`，而运行时
# 装不了包。只放这两个——esbuild 之外的打包器一律不预装，免得镜像无限膨胀、
# 也免得模型以为 `npm install` 能用。
RUN npm install -g --no-fund --no-audit typescript@5 tsx@4 \
 && npm cache clean --force

# -------------------------------------------------- Maven 离线仓库预热
#
# 三步都要，缺一不可：
#   1) go-offline 拉依赖树
#   2) package 走完编译+测试生命周期，把 compiler/surefire/resources 插件拉全
#   3) 对不参与默认生命周期的插件显式 `X:help`，否则离线时一用就 not found
#
# 预热的坐标清单与理由见 m2-seed/pom.xml。lastUpdated 是失败标记，留着会让
# 离线解析直接判死，必须删掉。
COPY m2-seed/pom.xml m2-seed/settings.xml /tmp/m2-seed/
COPY m2-seed/src /tmp/m2-seed/src
RUN cd /tmp/m2-seed \
 && mvn -B -ntp -s settings.xml -Dmaven.repo.local=/opt/m2 dependency:go-offline \
 && mvn -B -ntp -s settings.xml -Dmaven.repo.local=/opt/m2 package \
 && mvn -B -ntp -s settings.xml -Dmaven.repo.local=/opt/m2 \
      org.apache.maven.plugins:maven-shade-plugin:3.5.3:help \
      org.apache.maven.plugins:maven-assembly-plugin:3.7.1:help \
      org.codehaus.mojo:exec-maven-plugin:3.2.0:help \
 && cd / && rm -rf /tmp/m2-seed \
 && find /opt/m2 -name '*.lastUpdated' -delete \
 && find /opt/m2 -name '_remote.repositories' -delete

# 运行时的 mvn 用这份 settings：离线、不访问网络、本地仓库锁在 /opt/m2。
# 特意放在 /opt/m2 **外面**——APP_AGENT_CODE_EXEC_M2_HOST 会把宿主 ~/.m2
# 挂到 /opt/m2 上，settings 若也在里面就会被一起盖掉，mvn 直接找不到文件。
COPY m2-runtime-settings.xml /opt/mvn-settings.xml

WORKDIR /work
