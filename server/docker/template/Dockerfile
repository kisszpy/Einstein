# 云沙箱自定义模板：在官方 code-interpreter 之上补 JDK 21 + Maven
#
# 为什么需要它：官方 base / code-interpreter-v1 只有 Python 与 Node，模型写 Java
# 会拿到 `java: command not found`。这个模板把三套运行时装齐，让云端和本地容器
# 的能力对齐——否则「换个部署方式，Java 就不能跑了」。
#
# 第二代运行时的硬要求（不满足会起不来，或起来后 sshd/chrony 抢不到 PID）：
#   - Ubuntu / Debian 系
#   - 保留 systemd、openssh-server、sudo、chrony、fuse3、iptables
#   - CMD ["/sbin/init"]
#
# 构建（两种都行，见同目录 README.md）：
#   e2b template build -c "/root/.e2b-templates/einstein-langs" -n einstein-langs
# 或走 OpenAPI CreateTemplate（适合 CI）。
FROM debian:13

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV TZ=Asia/Shanghai

# systemd 及其配套组件不能省：第二代运行时靠 /sbin/init 拉起沙箱内的服务
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd systemd-sysv openssh-server sudo chrony fuse3 iptables \
      ca-certificates curl gnupg tzdata git unzip \
      python3 python3-pip python3-venv \
      openjdk-21-jdk-headless \
      nodejs npm \
 && rm -rf /var/lib/apt/lists/*

# Maven 用官方二进制而不是 apt 的包：Debian 的 maven 把 surefire 钉在 2.17
# （2014 年版本，不认 JUnit 5），`mvn test` 会直接假失败。官方 3.9.9 默认
# surefire 3.2.5，与模型最常见的 pom 写法对得上。
ARG MAVEN_VERSION=3.9.9
RUN curl -fsSL "https://dlcdn.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
      -o /tmp/maven.tar.gz \
 && mkdir -p /opt/maven \
 && tar -xzf /tmp/maven.tar.gz -C /opt/maven --strip-components=1 \
 && rm /tmp/maven.tar.gz \
 && ln -sfn /opt/maven/bin/mvn /usr/local/bin/mvn \
 && mvn -v | head -1

# 与本地镜像保持一致的数据处理栈：模型最常写的其实就是这些
RUN pip3 install --no-cache-dir --break-system-packages \
      pandas numpy openpyxl python-docx python-pptx matplotlib tabulate

# 项目里的 .ts 直接跑，不用先编译一遍
RUN npm install -g --silent typescript tsx && npm cache clean --force

# 云端默认允许出网，Maven 在线解析即可；这里不清本地仓库，留出「离线也跑得动」的余量
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV MAVEN_OPTS="-Xmx512m -Dfile.encoding=UTF-8"
# 沙箱内的工作目录，与 APP_AGENT_CODE_EXEC_E2B_WORKDIR 保持一致
RUN mkdir -p /home/user/work && chmod 777 /home/user/work

WORKDIR /home/user
CMD ["/sbin/init"]
