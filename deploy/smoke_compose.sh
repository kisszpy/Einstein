#!/usr/bin/env bash
# 生产编排冒烟：起容器 → 健康检查 → 端到端问答。
#
#   bash deploy/smoke_compose.sh              # 构建并起栈，跑完整冒烟，结束后保留容器
#   bash deploy/smoke_compose.sh --no-build   # 用已有镜像，不重新构建
#   bash deploy/smoke_compose.sh --infra-only # 只验基础设施与登录，不碰 LLM/嵌入
#   bash deploy/smoke_compose.sh --down       # 跑完把栈停掉（保留数据卷）
#   bash deploy/smoke_compose.sh --purge      # 跑完停栈并删数据卷（清空一切）
#   bash deploy/smoke_compose.sh --no-compose # 不碰 docker，只打已运行的后端（可配 SMOKE_BASE）
#
# 需要 LLM 与嵌入端点可达，否则摄入与对话会 503；只想验编排本身请加 --infra-only。
# 默认账号 admin/admin123，可用 SMOKE_USER / SMOKE_PASS 覆盖。

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.prod.yml"
ENV_FILE="$ROOT/deploy/.env"

BACKEND_PORT="${BACKEND_PORT:-8000}"
WEB_PORT="${WEB_PORT:-80}"
BASE="${SMOKE_BASE:-http://127.0.0.1:${BACKEND_PORT}}"
WEB="http://127.0.0.1:${WEB_PORT}"
SMOKE_USER="${SMOKE_USER:-admin}"
SMOKE_PASS="${SMOKE_PASS:-admin123}"
WAIT_SECONDS="${WAIT_SECONDS:-180}"

DO_BUILD=1
INFRA_ONLY=0
NO_COMPOSE=0
TEARDOWN=""
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    --infra-only) INFRA_ONLY=1 ;;
    --no-compose) NO_COMPOSE=1 ;;
    --down) TEARDOWN="down" ;;
    --purge) TEARDOWN="purge" ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$arg（用 -h 看用法）" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_NO=$'\033[31m'; C_SK=$'\033[33m'; C_DIM=$'\033[2m'; C_END=$'\033[0m'
else
  C_OK=; C_NO=; C_SK=; C_DIM=; C_END=
fi

PASS=0; FAIL=0; SKIP=0
step() { printf '\n%s▶ %s%s\n' "$C_DIM" "$1" "$C_END"; }
ok()   { printf '  %s✓%s %s\n' "$C_OK" "$C_END" "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  %s✗%s %s\n' "$C_NO" "$C_END" "$1"; FAIL=$((FAIL + 1)); }
skip() { printf '  %s-%s %s\n' "$C_SK" "$C_END" "$1"; SKIP=$((SKIP + 1)); }
die()  { printf '\n%s致命：%s%s\n' "$C_NO" "$1" "$C_END" >&2; exit 1; }

# ---------------------------------------------------------------- 工具函数

# 本机代理会劫持 127.0.0.1 请求，统一绕开
CURL=(curl -s --noproxy '*' --max-time 60)
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done

jget() { # jget <key> <json>
  [ -n "$PY" ] || return 0
  printf '%s' "$2" | "$PY" -c 'import sys,json
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
v=d
for part in sys.argv[1].split("."):
    if isinstance(v,dict):
        v=v.get(part,"")
    else:
        v=""; break
print(v if v is not None else "")' "$1" 2>/dev/null
}

http_code() { # http_code <method> <url> [curl args...]
  local method="$1" url="$2"; shift 2
  "${CURL[@]}" -o /dev/null -w '%{http_code}' -X "$method" "$url" "$@"
}

wait_http() { # wait_http <url> <want_code> <label> [seconds]
  local url="$1" want="$2" label="$3" limit="${4:-$WAIT_SECONDS}"
  local waited=0 code=""
  while [ "$waited" -lt "$limit" ]; do
    code=$(http_code GET "$url" || true)
    [ "$code" = "$want" ] && { ok "$label（${waited}s）"; return 0; }
    sleep 3; waited=$((waited + 3))
  done
  bad "$label：${limit}s 内未返回 $want（最后 $code）"
  return 1
}

compose() { "${DC[@]}" -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

# ---------------------------------------------------------------- 0. 前置检查

step "0. 前置检查"
command -v curl >/dev/null 2>&1 || die "未找到 curl"
if [ "$NO_COMPOSE" -eq 0 ]; then
  command -v docker >/dev/null 2>&1 || die "未找到 docker"
  if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
  else
    die "未找到 docker compose"
  fi
  ok "docker $("${DC[@]}" version --short 2>/dev/null | head -1)"

  [ -f "$COMPOSE_FILE" ] || die "缺少 $COMPOSE_FILE"
  if [ ! -f "$ENV_FILE" ]; then
    cp "$ROOT/deploy/.env.example" "$ENV_FILE" || die "无法创建 $ENV_FILE"
    printf '  %s注意：%s 已从 .env.example 生成，LLM / 嵌入端点用的是默认值\n' "$C_SK" "$C_END"
    skip "deploy/.env 不存在，已按模板创建"
  else
    ok "deploy/.env 就绪"
  fi
else
  DC=()
  ok "跳过 docker 检查（--no-compose），目标 $BASE"
fi
[ -n "$PY" ] || skip "无 python，JSON 解析降级（部分断言只能看 HTTP 码）"

# ---------------------------------------------------------------- 1. 起栈

if [ "$NO_COMPOSE" -eq 0 ]; then
  step "1. 启动编排"
  up_args=(up -d --remove-orphans)
  [ "$DO_BUILD" -eq 1 ] && up_args+=(--build)
  if ! compose "${up_args[@]}" 2>&1 | tail -20; then
    die "docker compose up 失败，先看 docker compose -f $COMPOSE_FILE logs"
  fi
  ok "编排已下发（8 个服务）"
else
  step "1. 跳过启动编排（--no-compose）"
fi

# ---------------------------------------------------------------- 2. 健康检查

step "2. 健康检查"
wait_http "$BASE/api/health" 200 "后端 /api/health" || true
if [ "$NO_COMPOSE" -eq 0 ]; then
  wait_http "$WEB/" 200 "前端首页" || true

  # 容器级状态：unhealthy / restarting 都算失败
  bad_ones=$(compose ps --format '{{.Service}} {{.Status}}' 2>/dev/null | grep -Ei 'unhealthy|restarting|exited' || true)
  if [ -z "$bad_ones" ]; then
    ok "全部容器处于运行态"
  else
    bad "异常容器：$(printf '%s' "$bad_ones" | tr '\n' ';')"
  fi
else
  skip "前端首页与容器状态（--no-compose）"
fi

# ---------------------------------------------------------------- 3. 登录

step "3. 登录与鉴权"
login_body=$("${CURL[@]}" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$SMOKE_USER\",\"password\":\"$SMOKE_PASS\"}")
TOKEN=$(jget token "$login_body")
if [ -n "$TOKEN" ]; then
  ok "登录成功（$SMOKE_USER）"
else
  bad "登录失败：$(printf '%s' "$login_body" | head -c 160)"
fi

if [ -n "$TOKEN" ]; then
  me=$(jget user.username "$("${CURL[@]}" "$BASE/api/auth/me" -H "Authorization: Bearer $TOKEN")")
  [ "$me" = "$SMOKE_USER" ] && ok "/api/auth/me 返回当前用户" || bad "/api/auth/me 返回异常（$me）"

  code=$(http_code GET "$BASE/api/kb")
  [ "$code" = "401" ] && ok "无令牌访问被拒（401）" || bad "无令牌访问未被拦截（$code）"
fi

if [ "$INFRA_ONLY" -eq 1 ]; then
  step "4. 跳过 LLM / 嵌入相关用例（--infra-only）"
  skip "建库 / 摄入 / 检索 / 对话"
elif [ -z "$TOKEN" ]; then
  skip "无令牌，后续业务用例全部跳过"
else

# ---------------------------------------------------------------- 4. 建库

step "4. 知识库"
AUTH=(-H "Authorization: Bearer $TOKEN")
KB_NAME="smoke-$(date +%s)"
kb_body=$("${CURL[@]}" -X POST "$BASE/api/kb" "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d "{\"name\":\"$KB_NAME\"}")
KB_ID=$(jget id "$kb_body")
if [ -n "$KB_ID" ]; then
  ok "建库 $KB_NAME"
else
  bad "建库失败：$(printf '%s' "$kb_body" | head -c 160)"
fi

# ---------------------------------------------------------------- 5. 同步摄入

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
cat > "$TMP_DIR/travel.txt" <<'TXT'
差旅报销流程说明
员工差旅报销需在出差结束后 10 个工作日内提交，逾期需部门负责人补充说明。
审批链路：直属主管 → 部门负责人 → 财务复核。
单笔超过 5000 元的报销需附发票原件扫描件。
TXT

if [ -n "$KB_ID" ]; then
  step "5. 同步摄入（小文件）"
  up_body=$("${CURL[@]}" -X POST "$BASE/api/kb/$KB_ID/documents/upload" "${AUTH[@]}" \
    -F "file=@$TMP_DIR/travel.txt")
  chunks=$(jget chunk_count "$up_body")
  if [ -n "$chunks" ] && [ "$chunks" -gt 0 ] 2>/dev/null; then
    ok "摄入成功，切片 $chunks 段（v$(jget version "$up_body")）"
  else
    case "$(printf '%s' "$up_body" | head -c 200)" in
      *嵌入*|*embedding*|*503*) skip "嵌入端点不可达，摄入跳过：$(printf '%s' "$up_body" | head -c 120)" ;;
      *) bad "摄入失败：$(printf '%s' "$up_body" | head -c 160)" ;;
    esac
  fi

  # ------------------------------------------------------------ 6. 异步摄入
  step "6. 异步摄入（队列 + worker）"
  cat > "$TMP_DIR/leave.txt" <<'TXT'
请假制度说明
年假按入职年限计算，满一年 5 天，满三年 10 天。
请假需在 OA 系统提交，三天以上需提前一周申请。
TXT
  async_body=$("${CURL[@]}" -X POST "$BASE/api/kb/$KB_ID/documents/async" "${AUTH[@]}" \
    -F "file=@$TMP_DIR/leave.txt")
  JOB_ID=$(jget job_id "$async_body")
  if [ -z "$JOB_ID" ]; then
    bad "入队失败：$(printf '%s' "$async_body" | head -c 160)"
  else
    ok "入队成功 job=$JOB_ID（$(jget backend "$async_body")）"
    waited=0; jstatus=""
    while [ "$waited" -lt 120 ]; do
      jbody=$("${CURL[@]}" "$BASE/api/jobs/$JOB_ID" "${AUTH[@]}")
      jstatus=$(jget status "$jbody")
      [ "$jstatus" = "done" ] || [ "$jstatus" = "failed" ] && break
      sleep 3; waited=$((waited + 3))
    done
    if [ "$jstatus" = "done" ]; then
      ok "worker 消费完成（${waited}s，progress=$(jget progress.percent "$jbody")%）"
    elif [ "$jstatus" = "failed" ]; then
      bad "任务失败：$(jget error "$jbody" | head -c 160)"
    else
      bad "任务 ${waited}s 内未完成（state=$jstatus）—— 检查 worker 是否在跑：docker compose -f $COMPOSE_FILE logs worker"
    fi
  fi

  # ------------------------------------------------------------ 7. 检索
  step "7. 检索"
  s_body=$("${CURL[@]}" -X POST "$BASE/api/kb/search" "${AUTH[@]}" -H 'Content-Type: application/json' \
    -d "{\"kb_ids\":[\"$KB_ID\"],\"query\":\"报销流程\",\"top_k\":3}")
  # /api/kb/search 直接返回数组
  hits=0
  if [ -n "$PY" ]; then
    hits=$(printf '%s' "$s_body" | "$PY" -c 'import sys,json
try: print(len(json.load(sys.stdin)))
except Exception: print(0)' 2>/dev/null)
  fi
  if [ "${hits:-0}" -gt 0 ] 2>/dev/null; then
    ok "命中 $hits 条"
  else
    bad "检索无命中：$(printf '%s' "$s_body" | head -c 160)"
  fi

  # ------------------------------------------------------------ 8. 对话 SSE
  step "8. 对话（SSE 流式）"
  sse=$("${CURL[@]}" --max-time 180 -N -X POST "$BASE/api/chat" "${AUTH[@]}" \
    -H 'Content-Type: application/json' \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"差旅报销的审批链路是什么？\"}],\"kb_ids\":[\"$KB_ID\"]}")
  if printf '%s' "$sse" | grep -q '^event: meta'; then ok "收到 meta 帧"; else bad "未收到 meta 帧"; fi
  if printf '%s' "$sse" | grep -q '^event: done'; then
    ok "收到 done 帧"
  elif printf '%s' "$sse" | grep -q '^event: error'; then
    bad "流中返回 error：$(printf '%s' "$sse" | grep '^data:' | tail -1 | head -c 160)"
  else
    bad "未收到 done 帧（连接可能被缓冲或超时）"
  fi
  answer_len=0
  if [ -n "$PY" ]; then
    answer_len=$(printf '%s' "$sse" | "$PY" -c 'import sys,json
n=0
for line in sys.stdin:
    if line.startswith("data:"):
        try: d=json.loads(line[5:])
        except Exception: continue
        t=d.get("text")
        if isinstance(t,str): n+=len(t)
print(n)' 2>/dev/null)
  fi
  if [ "${answer_len:-0}" -gt 0 ] 2>/dev/null; then
    ok "回答正文 ${answer_len} 字"
  else
    bad "没有收到正文 delta"
  fi

  # ------------------------------------------------------------ 收尾
  step "9. 清理测试数据"
  code=$(http_code DELETE "$BASE/api/kb/$KB_ID" "${AUTH[@]}")
  [ "$code" = "200" ] && ok "已删除测试知识库" || bad "删除知识库失败（$code）"
fi
fi

# ---------------------------------------------------------------- 停栈

if [ "$TEARDOWN" = "down" ] && [ "$NO_COMPOSE" -eq 0 ]; then
  step "停栈"
  compose down && ok "已停止（数据卷保留）"
elif [ "$TEARDOWN" = "purge" ] && [ "$NO_COMPOSE" -eq 0 ]; then
  step "停栈并清空数据"
  compose down -v && ok "已停止并删除数据卷"
fi

# ---------------------------------------------------------------- 汇总

printf '\n%s汇总：%s通过 %d · 失败 %d · 跳过 %d\n' "$C_DIM" "$C_END" "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
  printf '%s冒烟未通过%s\n' "$C_NO" "$C_END"
  exit 1
fi
printf '%s冒烟通过%s\n' "$C_OK" "$C_END"
exit 0
