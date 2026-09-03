"""Vercel 서버리스 함수 — POST /ai/chat/stream (SSE).

Vercel 문서에 "You can stream responses from Vercel Functions that use the Python
runtime" 이라고 명시돼 있고, 변경 이력상 기본 활성화라 `VERCEL_FORCE_PYTHON_STREAMING`
설정도 필요 없다. 다만 이 핸들러 방식(BaseHTTPRequestHandler 에 조금씩 write)이
실제로 흘러가는지는 배포해 봐야 안다.

그래서 화면은 이 엔드포인트가 실패하면 `/ai/chat` 단발 응답으로 넘어간다.
흘러가지 않더라도 답변은 그대로 나오고, 타이핑 효과만 없다.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.core import handle_chat_stream  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 — Vercel 이 요구하는 이름
    # 응답을 모아 보내면 스트리밍이 스트리밍이 아니게 된다.
    wbufsize = 0

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            self._fail(type(exc).__name__, str(exc)[:300])
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()

        def emit(event: str, data: dict) -> None:
            body = json.dumps(data, ensure_ascii=False)
            self.wfile.write(("event: " + event + "\ndata: " + body + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            handle_chat_stream(payload, emit)
        except (BrokenPipeError, ConnectionAbortedError):
            pass  # 사용자가 닫았다
        except Exception as exc:  # noqa: BLE001
            try:
                emit("done", {"llm": "error", "detail": str(exc)[:300]})
            except Exception:  # noqa: BLE001
                pass

    def _fail(self, kind: str, detail: str) -> None:
        body = json.dumps({"error": kind, "detail": detail}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
