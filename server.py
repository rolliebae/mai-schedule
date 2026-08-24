#!/usr/bin/env python3
"""Local server for the MAI schedule utility.

Serves the static v4 UI and exposes one small same-origin API used for live
checks against public.mai.ru. The existing compressed database continues to
power group and room tools in the browser.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from schedule import (
    DEFAULT_GROUP,
    DEFAULT_QUERY,
    DEFAULT_SEMESTER_END,
    DEFAULT_SEMESTER_START,
    build_schedule_url,
    fetch_schedule,
    find_matches,
    format_period,
    in_semester,
    parse_iso_date,
    web_result_item,
)

ROOT = Path(__file__).resolve().parent


def _dedupe_results(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = json.dumps(
            {
                "date": item.get("date"),
                "title": item.get("title"),
                "details": item.get("details"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "MAISchedule/4.1.6"

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal quiet unless something actually fails.
        return

    def _send_bytes(
        self,
        data: bytes,
        *,
        status: int = 200,
        content_type: str = "application/octet-stream",
        cache_control: str = "no-cache",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(
            body,
            status=status,
            content_type="application/json; charset=utf-8",
            cache_control="no-store",
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "version": "4.1.6"})
            return

        path = unquote(parsed.path)
        if path == "/":
            path = "/index.html"

        candidate = (ROOT / path.lstrip("/")).resolve()
        if ROOT not in candidate.parents and candidate != ROOT:
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return

        content_type, _ = mimetypes.guess_type(candidate.name)
        content_type = content_type or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        # This is a local development utility. Avoid browser caches entirely: database_v413.js
        # and scripts.js can change between two starts after a database rebuild.
        self._send_bytes(candidate.read_bytes(), content_type=content_type, cache_control="no-store")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/live-check":
            self.send_error(404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))

            group = str(payload.get("group") or DEFAULT_GROUP).strip().upper()
            query = str(payload.get("query") or DEFAULT_QUERY).strip()
            start = parse_iso_date(
                str(payload.get("start") or DEFAULT_SEMESTER_START.isoformat())
            )
            end = parse_iso_date(
                str(payload.get("end") or DEFAULT_SEMESTER_END.isoformat())
            )

            if not group or not query:
                raise ValueError("Заполни группу и преподавателя")
            if start > end:
                raise ValueError("Дата начала позже даты окончания")

            data = fetch_schedule(group)
            matches = find_matches(data, query)
            selected = [m for m in matches if in_semester(m, start, end) is True]
            rendered = _dedupe_results(
                [web_result_item(m, query, start, end) for m in selected]
            )

            self.send_json(
                {
                    "ok": True,
                    "source": "МАИ онлайн",
                    "source_url": build_schedule_url(group),
                    "group": group,
                    "query": query,
                    "period": format_period(start, end),
                    "count": len(rendered),
                    "raw_match_count": len(selected),
                    "total_matches": len(matches),
                    "matches": rendered,
                }
            )
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                message = "Расписание этой группы не найдено на public.mai.ru"
            else:
                message = f"МАИ вернул HTTP {status or '?'}"
            self.send_json({"ok": False, "error": message}, 502)
        except requests.RequestException:
            self.send_json(
                {"ok": False, "error": "Не удалось подключиться к public.mai.ru"},
                502,
            )
        except (argparse.ArgumentTypeError, ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)


def run(host: str, port: int, open_browser: bool) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"MAI Schedule 4.1.6  {url}")
    print("Ctrl+C — остановить")
    if open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Запустить локальный интерфейс расписания МАИ")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    run(args.host, args.port, not args.no_browser)
