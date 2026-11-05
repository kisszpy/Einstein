"""验证 /v1/chat/completions 的流式（OpenAI 兼容帧）。"""

import json
import urllib.request

BASE = "http://127.0.0.1:8000"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path: str, body: dict, headers: dict | None = None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body, ensure_ascii=False).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return op.open(req, timeout=300)


with call("/api/apikeys", {"name": "流式测试"}) as resp:
    token = json.loads(resp.read())["token"]

req = urllib.request.Request(
    BASE + "/v1/chat/completions",
    data=json.dumps(
        {"messages": [{"role": "user", "content": "用一句话说明什么是互斥条件"}], "stream": True},
        ensure_ascii=False,
    ).encode(),
    method="POST",
)
req.add_header("Content-Type", "application/json")
req.add_header("Authorization", f"Bearer {token}")

frames, text = 0, []
with op.open(req, timeout=300) as resp:
    print("content-type:", resp.headers.get("content-type"))
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        frames += 1
        if payload == "[DONE]":
            print("收到 [DONE] ✓")
            break
        d = json.loads(payload)
        delta = d["choices"][0]["delta"]
        if delta.get("content"):
            text.append(delta["content"])
        if frames <= 3:
            print(f"frame {frames}: object={d['object']} delta={delta}")
print("帧数:", frames)
print("拼接结果:", "".join(text)[:120])
