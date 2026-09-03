"""화면과 에이전트를 한 프로세스로 띄운다.

`python -m http.server` 는 정적 파일만 준다. 채팅을 붙이려면 POST 를 받을 곳이 필요한데,
발표 때 서버를 두 개 띄우고 포트를 맞추는 일은 실수하기 좋다. 그래서 web/ 정적 서빙과
`POST /ai/chat` 을 한 곳에서 처리한다.

    python scripts/serve_agent.py            # http://localhost:5173
    python scripts/serve_agent.py --port 8080

GEMINI_API_KEY 가 없어도 뜬다. 그 경우 에이전트는 화면 자료만으로 답한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.core import handle_chat, handle_chat_stream, load_env_files  # noqa: E402

WEB = ROOT / "web"


class Handler(SimpleHTTPRequestHandler):
    # 기본값은 응답을 모아 보낸다. 그러면 스트리밍이 스트리밍이 아니게 된다.
    wbufsize = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_POST(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler 규약
        path = self.path.rstrip("/")
        if path not in ("/ai/chat", "/ai/chat/stream"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            self._json({"error": type(exc).__name__, "detail": str(exc)[:400]})
            return

        if path == "/ai/chat/stream":
            self._stream(payload)
            return
        try:
            body = handle_chat(payload)
        except Exception as exc:  # noqa: BLE001 — 채팅 오류가 화면을 멈추게 두지 않는다
            body = {"error": type(exc).__name__, "detail": str(exc)[:400]}
        self._json(body)

    def _stream(self, payload: dict) -> None:
        """SSE. 근거를 먼저 보내고 답변을 조각으로 이어 보낸다."""
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()

        def emit(event: str, data: dict) -> None:
            payload = json.dumps(data, ensure_ascii=False)
            chunk = "event: " + event + "\ndata: " + payload + "\n\n"
            self.wfile.write(chunk.encode("utf-8"))
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

    def _json(self, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self) -> None:
        # 자료 파일은 자주 바뀐다. 캐시된 옛 인덱스를 읽어 필지가 안 뜨는 식으로
        # 조용히 깨진 적이 있어 정적 응답도 캐시하지 않는다.
        self.send_header("cache-control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        if "/ai/chat" in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="물길잡이 화면 + 에이전트 서버")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    load_env_files()
    import os
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

    print(f"물길잡이  http://{args.host}:{args.port}")
    print(f"  에이전트  POST /ai/chat")
    print(f"  LLM       {model if has_key else '키 없음 — 화면 자료만으로 답변'}")
    ThreadingHTTPServer((args.host, args.port), partial(Handler)).serve_forever()


if __name__ == "__main__":
    main()
