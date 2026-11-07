"""Einstein 平台客户端。

设计取舍：
- 只用标准库，装到任何环境都不会跟依赖打架
- 幂等重试只针对网络错误与 5xx，4xx（参数错、越权）直接抛，不掩盖问题
- 流式接口统一暴露成迭代器，调用方自己决定是打印还是聚合
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


class EinsteinError(RuntimeError):
    def __init__(self, message: str, status: int = 0, detail: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


@dataclass
class JobResult:
    job_id: str
    status: str
    result: dict = field(default_factory=dict)
    error: str = ""
    progress: dict = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.status in ("done", "failed")


class EinsteinClient:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        token: str | None = None,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        workspace: str | None = None,
        timeout: float = 120.0,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = max(1, retries)
        self.workspace = workspace
        self._token = token or api_key
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if username and password:
            self.login(username, password)

    # ------------------------------------------------------------ 基础设施
    def __enter__(self) -> "EinsteinClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self.workspace:
            headers["X-Workspace"] = self.workspace
        headers.update(extra or {})
        return headers

    def _request(
        self, method: str, path: str, body: dict | None = None,
        *, raw: bytes | None = None, content_type: str = "application/json",
    ) -> Any:
        data = raw if raw is not None else (
            json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        )
        headers = self._headers({"Content-Type": content_type} if data is not None else None)

        last: Exception | None = None
        for attempt in range(self.retries):
            req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    payload = resp.read()
                    if not payload:
                        return None
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        return payload
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                # 4xx 是调用方的问题，重试没有意义
                if exc.code < 500:
                    raise EinsteinError(f"{method} {path} -> {exc.code}: {detail}", exc.code, detail) from exc
                last = EinsteinError(f"{method} {path} -> {exc.code}", exc.code, detail)
            except (urllib.error.URLError, TimeoutError) as exc:
                last = exc
            if attempt < self.retries - 1:
                time.sleep(min(2 ** attempt, 8))
        raise EinsteinError(f"{method} {path} 失败：{last}")

    def _stream(self, path: str, body: dict) -> Iterator[tuple[str, dict]]:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body, ensure_ascii=False).encode(),
            method="POST",
            headers=self._headers({"Content-Type": "application/json", "Accept": "text/event-stream"}),
        )
        with self._opener.open(req, timeout=self.timeout) as resp:
            event, data_lines = "", []
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if data_lines:
                        text = "\n".join(data_lines)
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError:
                            payload = {"text": text}
                        yield event or "message", payload
                    event, data_lines = "", []
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

    # ------------------------------------------------------------ 认证
    def login(self, username: str, password: str) -> dict:
        data = self._request("POST", "/api/auth/login", {"username": username, "password": password})
        self._token = data["token"]
        return data

    def me(self) -> dict:
        return self._request("GET", "/api/auth/me")

    # ------------------------------------------------------------ 对话
    def chat_stream(
        self, message: str, *, history: list[dict] | None = None,
        kb_ids: list[str] | None = None, expert_id: str | None = None,
        web_mode: str = "auto", conversation_id: str | None = None,
    ) -> Iterator[tuple[str, dict]]:
        messages = list(history or []) + [{"role": "user", "content": message}]
        yield from self._stream("/api/chat", {
            "conversation_id": conversation_id,
            "messages": messages,
            "kb_ids": kb_ids or [],
            "expert_id": expert_id,
            "web_mode": web_mode,
        })

    def chat(self, message: str, **kwargs: Any) -> str:
        """聚合流式回答为完整字符串。"""
        parts: list[str] = []
        for event, data in self.chat_stream(message, **kwargs):
            if event == "delta":
                parts.append(data.get("text", ""))
            elif event == "error":
                # 服务端 error 帧用 message 字段；兼容老版本的 detail
                raise EinsteinError(data.get("message") or data.get("detail") or "对话失败", detail=data)
        return "".join(parts)

    # ------------------------------------------------------------ 检索
    def search(self, kb_ids: list[str], query: str, top_k: int = 4, mode: str | None = None) -> list[dict]:
        return self._request("POST", "/api/kb/search", {
            "kb_ids": kb_ids, "query": query, "top_k": top_k, "mode": mode,
        })

    # ------------------------------------------------------------ 智能体
    def agent_events(
        self, task: str, *, tool_ids: list[str] | None = None,
        kb_ids: list[str] | None = None, session_id: str = "", project: str = "",
    ) -> Iterator[tuple[str, dict]]:
        yield from self._stream("/api/agent/run", {
            "task": task, "tool_ids": tool_ids or [], "kb_ids": kb_ids or [],
            "session_id": session_id, "project": project,
        })

    def agent(self, task: str, **kwargs: Any) -> str:
        parts: list[str] = []
        for event, data in self.agent_events(task, **kwargs):
            if event == "delta":
                parts.append(data.get("text", ""))
            elif event == "done":
                return data.get("answer") or "".join(parts)
            elif event == "error":
                raise EinsteinError(data.get("message") or data.get("detail") or "任务失败", detail=data)
        return "".join(parts)

    # ------------------------------------------------------------ 知识库
    def list_kbs(self) -> list[dict]:
        return self._request("GET", "/api/kb")

    def create_kb(self, name: str) -> dict:
        return self._request("POST", "/api/kb", {"name": name})

    def add_text(self, kb_id: str, name: str, content: str, **chunk_opts: Any) -> dict:
        return self._request("POST", f"/api/kb/{kb_id}/documents", {
            "name": name, "content": content, **chunk_opts,
        })

    def upload_document(self, kb_id: str, path: str | Path, *, wait: bool = False,
                        poll_interval: float = 2.0, timeout: float = 900.0) -> JobResult:
        """上传并异步摄取。wait=True 时轮询到任务结束。"""
        file_path = Path(path)
        boundary = "----einstein" + uuid.uuid4().hex
        ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\";"
            f" filename=\"{file_path.name}\"\r\nContent-Type: {ctype}\r\n\r\n"
        ).encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

        data = self._request(
            "POST", f"/api/kb/{kb_id}/documents/async", raw=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        job = JobResult(job_id=data["job_id"], status=data.get("status", "pending"))
        return self.wait_job(job.job_id, poll_interval=poll_interval, timeout=timeout) if wait else job

    # ------------------------------------------------------------ 任务
    def get_job(self, job_id: str) -> JobResult:
        data = self._request("GET", f"/api/jobs/{job_id}")
        return JobResult(
            job_id=data["id"], status=data["status"], result=data.get("result") or {},
            error=data.get("error", ""), progress=data.get("progress") or {},
        )

    def wait_job(self, job_id: str, *, poll_interval: float = 2.0, timeout: float = 900.0) -> JobResult:
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.get_job(job_id)
            if job.done:
                return job
            time.sleep(poll_interval)
        raise EinsteinError(f"任务 {job_id} 超时（{timeout}s）")

    def list_documents(self, kb_id: str) -> list[dict]:
        return self._request("GET", f"/api/kb/{kb_id}/documents")

    def document_chunks(self, kb_id: str, doc_id: str) -> list[dict]:
        return self._request("GET", f"/api/kb/{kb_id}/documents/{doc_id}/chunks")

    def patch_chunk(self, kb_id: str, chunk_id: str, text: str) -> dict:
        return self._request("PATCH", f"/api/kb/{kb_id}/chunks/{chunk_id}", {"text": text})

    # ------------------------------------------------------------ 平台
    def list_jobs(self, status: str | None = None, limit: int = 50) -> list[dict]:
        query = urllib.parse.urlencode({
            k: v for k, v in {"status": status, "limit": limit}.items() if v is not None
        })
        return self._request("GET", f"/api/jobs?{query}")

    def audit(self, limit: int = 100) -> dict:
        return self._request("GET", f"/api/audit?limit={limit}")
