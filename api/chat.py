"""Vercel 서버리스 함수 — POST /ai/chat.

로컬은 `scripts/serve_agent.py` 가 정적 파일과 채팅을 함께 처리한다.
Vercel 은 정적 파일을 CDN 이 주고 채팅만 이 함수가 받는다.

스트리밍은 `api/chat_stream.py` 가 맡는다. 이 파일은 그것이 동작하지 않는 환경을 위한
경로다 — 화면은 스트리밍이 실패하면 여기로 넘어오고, 답변은 그대로 나온다.

의존성이 없다. `src/agent/core.py` 는 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.core import handle_chat  # noqa: E402


class handler(BaseHTTPRequestHandler):  # noqa: N801 — Vercel 이 요구하는 이름
    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            body = handle_chat(payload)
        except Exception as exc:  # noqa: BLE001 — 채팅 오류로 화면을 멈추지 않는다
            body = {"error": type(exc).__name__, "detail": str(exc)[:400]}
        self._send(body)

    def _send(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
