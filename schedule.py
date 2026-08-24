#!/usr/bin/env python3
"""MAI schedule checker.

A small CLI for checking whether a teacher/name fragment appears in a MAI
schedule. Defaults are tailored to group М9О-217БВ-25, 3rd semester
(autumn 2026/27), query "Зверев".

Examples:
    python schedule.py
    python schedule.py --cli --query Зверев
    python schedule.py --cli --group М9О-217БВ-25 --query Зверев
    python schedule.py --input-json schedule.json
    python schedule.py --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import webbrowser
import os
import re
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://public.mai.ru/schedule/data/{md5}.json"
DEFAULT_GROUP = "М9О-217БВ-25"
DEFAULT_QUERY = "Зверев"
DEFAULT_SEMESTER_START = date(2026, 9, 1)
DEFAULT_SEMESTER_END = date(2027, 1, 31)

RUS_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}

SHORT_MONTHS = {
    1: "янв",
    2: "фев",
    3: "мар",
    4: "апр",
    5: "май",
    6: "июн",
    7: "июл",
    8: "авг",
    9: "сен",
    10: "окт",
    11: "ноя",
    12: "дек",
}

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

DATE_PATTERNS = [
    re.compile(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b"),
    re.compile(
        r"\b(\d{1,2})\s+"
        + "(" + "|".join(RUS_MONTHS) + ")"
        + r"\s+(20\d{2})\b",
        re.IGNORECASE,
    ),
]

TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[-–—]\s*(?:[01]?\d|2[0-3]):[0-5]\d)?\b")


@dataclass
class Match:
    path: str
    value: str
    context_path: str
    context: Any
    dates: list[date]


class UI:
    """Restrained, dependency-free terminal UI."""

    def __init__(self, *, color: bool = True) -> None:
        self.color = (
            color
            and sys.stdout.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM", "").lower() != "dumb"
        )
        self.width = min(max(shutil.get_terminal_size((88, 24)).columns, 64), 110)

    def paint(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def muted(self, text: str) -> str:
        return self.paint(text, "2")

    def strong(self, text: str) -> str:
        return self.paint(text, "1")

    def ok(self, text: str) -> str:
        return self.paint(text, "32")

    def warn(self, text: str) -> str:
        return self.paint(text, "33")

    def bad(self, text: str) -> str:
        return self.paint(text, "31")

    def accent(self, text: str) -> str:
        return self.paint(text, "36")

    def rule(self) -> None:
        print(self.muted("─" * self.width))

    def heading(self, group: str, start: date, end: date) -> None:
        print()
        print(self.strong("МАИ / расписание"))
        print(
            self.muted(
                f"{group} · 3-й семестр · {format_period(start, end)}"
            )
        )
        self.rule()

    def pair(self, label: str, value: str) -> None:
        print(f"{self.muted(label.ljust(10))} {value}")

    def error(self, title: str, detail: str | None = None) -> None:
        print()
        print(self.bad(title), file=sys.stderr)
        if detail:
            print(self.muted(detail), file=sys.stderr)


WEB_HTML = r"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>МАИ / расписание</title>
  <style>
    :root {
      --bg: #f5f5f2;
      --surface: #ffffff;
      --text: #181817;
      --muted: #71716b;
      --line: #deded8;
      --line-strong: #c6c6bf;
      --accent: #1f5eff;
      --danger: #b42318;
      --success: #176b3a;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Arial, sans-serif;
    }
    main {
      width: min(920px, calc(100% - 32px));
      margin: 56px auto 80px;
    }
    header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 16px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--line-strong);
    }
    h1 {
      margin: 0;
      font-size: 22px;
      line-height: 1.2;
      letter-spacing: -0.02em;
      font-weight: 650;
    }
    .subtle { color: var(--muted); font-size: 13px; }
    form {
      display: grid;
      grid-template-columns: 1.35fr 1fr 1fr 1fr auto;
      gap: 10px;
      align-items: end;
      padding: 24px 0;
      border-bottom: 1px solid var(--line);
    }
    label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 6px 1px;
    }
    input {
      width: 100%;
      height: 40px;
      border: 1px solid var(--line-strong);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 11px;
      font: inherit;
      outline: none;
    }
    input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(31,94,255,.10);
    }
    button {
      height: 40px;
      border: 0;
      border-radius: 6px;
      background: var(--text);
      color: white;
      padding: 0 16px;
      font: 600 14px/1 inherit;
      cursor: pointer;
      white-space: nowrap;
    }
    button:hover { background: #30302e; }
    button:disabled { opacity: .48; cursor: default; }
    #status {
      min-height: 64px;
      padding: 22px 0 14px;
    }
    .status-main {
      font-size: 18px;
      font-weight: 650;
      letter-spacing: -0.01em;
    }
    .status-meta { color: var(--muted); margin-top: 4px; font-size: 13px; }
    .success { color: var(--success); }
    .danger { color: var(--danger); }
    .result-list { border-top: 1px solid var(--line); }
    .result {
      display: grid;
      grid-template-columns: 150px 1fr;
      gap: 22px;
      padding: 18px 0;
      border-bottom: 1px solid var(--line);
    }
    .date {
      color: var(--muted);
      font-size: 13px;
      padding-top: 2px;
    }
    .teacher { font-weight: 650; margin-bottom: 3px; }
    .detail { color: #3f3f3b; }
    .empty {
      padding: 28px 0;
      color: var(--muted);
      border-top: 1px solid var(--line);
    }
    details {
      margin-top: 20px;
      color: var(--muted);
      font-size: 13px;
    }
    summary { cursor: pointer; user-select: none; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #ecece7;
      color: #44443f;
      border-radius: 6px;
      padding: 12px;
      overflow: auto;
      font: 12px/1.5 "SFMono-Regular", Consolas, monospace;
    }
    @media (max-width: 820px) {
      main { margin-top: 28px; }
      form { grid-template-columns: 1fr 1fr; }
      .wide { grid-column: 1 / -1; }
      button { width: 100%; }
    }
    @media (max-width: 560px) {
      main { width: min(100% - 24px, 920px); }
      header { display: block; }
      header .subtle { margin-top: 6px; }
      form { grid-template-columns: 1fr; }
      .wide { grid-column: auto; }
      .result { grid-template-columns: 1fr; gap: 5px; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <h1>МАИ / расписание</h1>
    <div class="subtle">Проверка преподавателя по всему JSON расписания</div>
  </header>

  <form id="form">
    <div class="wide">
      <label for="group">Группа</label>
      <input id="group" name="group" value="М9О-217БВ-25" autocomplete="off">
    </div>
    <div>
      <label for="query">Преподаватель</label>
      <input id="query" name="query" value="Зверев" autocomplete="off">
    </div>
    <div>
      <label for="start">С</label>
      <input id="start" name="start" type="date" value="2026-09-01">
    </div>
    <div>
      <label for="end">По</label>
      <input id="end" name="end" type="date" value="2027-01-31">
    </div>
    <button id="submit" type="submit">Проверить</button>
  </form>

  <section id="status">
    <div class="status-main">Готово к проверке</div>
    <div class="status-meta">3-й семестр · осень 2026/27</div>
  </section>

  <section id="results"></section>
</main>
<script>
const form = document.getElementById('form');
const status = document.getElementById('status');
const results = document.getElementById('results');
const submit = document.getElementById('submit');

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submit.disabled = true;
  submit.textContent = 'Проверяю…';
  results.innerHTML = '';
  status.innerHTML = '<div class="status-main">Получаю расписание</div><div class="status-meta">public.mai.ru</div>';

  const payload = {
    group: document.getElementById('group').value.trim(),
    query: document.getElementById('query').value.trim(),
    start: document.getElementById('start').value,
    end: document.getElementById('end').value
  };

  try {
    const response = await fetch('/api/check', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Не удалось проверить расписание');

    const cls = data.count > 0 ? 'success' : '';
    const title = data.count > 0
      ? `${esc(data.query)}: найдено ${data.count}`
      : `${esc(data.query)}: не найден`;
    status.innerHTML = `<div class="status-main ${cls}">${title}</div>` +
      `<div class="status-meta">${esc(data.group)} · ${esc(data.period)} · всего совпадений в JSON: ${data.total}</div>`;

    if (!data.matches.length) {
      results.innerHTML = '<div class="empty">В выбранном периоде совпадений нет.</div>';
      return;
    }

    results.innerHTML = '<div class="result-list">' + data.matches.map(item => `
      <article class="result">
        <div class="date">${esc(item.date)}</div>
        <div>
          <div class="teacher">${esc(item.title)}</div>
          ${item.details.map(x => `<div class="detail">${esc(x)}</div>`).join('')}
          <details>
            <summary>Технические данные</summary>
            <pre>${esc(JSON.stringify(item.context, null, 2))}</pre>
          </details>
        </div>
      </article>`).join('') + '</div>';
  } catch (error) {
    status.innerHTML = `<div class="status-main danger">Не удалось проверить</div><div class="status-meta">${esc(error.message)}</div>`;
  } finally {
    submit.disabled = false;
    submit.textContent = 'Проверить';
  }
});
</script>
</body>
</html>"""


def md5_group(group_name: str) -> str:
    return hashlib.md5(group_name.strip().encode("utf-8")).hexdigest()


def build_schedule_url(group_name: str) -> str:
    return BASE_URL.format(md5=md5_group(group_name))


def fetch_schedule(group_name: str, timeout: tuple[int, int] = (10, 30)) -> Any:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://mai.ru/",
    }
    response = requests.get(
        build_schedule_url(group_name),
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except requests.JSONDecodeError as exc:
        preview = response.text[:240].replace("\n", " ")
        raise RuntimeError(f"Сервер вернул не JSON: {preview!r}") from exc


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def iter_strings(
    value: Any,
    path: str = "$",
    ancestors: list[tuple[str, Any]] | None = None,
):
    if ancestors is None:
        ancestors = []

    if isinstance(value, dict):
        new_ancestors = ancestors + [(path, value)]
        for key, item in value.items():
            key_path = f"{path}.{key}"
            if isinstance(key, str):
                yield key_path + "<key>", key, new_ancestors
            yield from iter_strings(item, key_path, new_ancestors)
    elif isinstance(value, list):
        new_ancestors = ancestors + [(path, value)]
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{path}[{index}]", new_ancestors)
    elif isinstance(value, str):
        yield path, value, ancestors


def parse_dates(text: str) -> list[date]:
    found: list[date] = []
    for pattern_index, pattern in enumerate(DATE_PATTERNS):
        for match in pattern.finditer(text):
            try:
                if pattern_index == 0:
                    year, month, day = map(int, match.groups())
                elif pattern_index == 1:
                    day, month, year = map(int, match.groups())
                else:
                    day = int(match.group(1))
                    month = RUS_MONTHS[match.group(2).lower()]
                    year = int(match.group(3))
                parsed = date(year, month, day)
                if parsed not in found:
                    found.append(parsed)
            except (ValueError, KeyError):
                pass
    return found


def collect_dates(value: Any) -> list[date]:
    dates: list[date] = []

    def add(text: str) -> None:
        for parsed in parse_dates(text):
            if parsed not in dates:
                dates.append(parsed)

    if isinstance(value, dict):
        for key, item in value.items():
            add(str(key))
            if isinstance(item, (dict, list)):
                for parsed in collect_dates(item):
                    if parsed not in dates:
                        dates.append(parsed)
            elif isinstance(item, str):
                add(item)
    elif isinstance(value, list):
        for item in value:
            for parsed in collect_dates(item):
                if parsed not in dates:
                    dates.append(parsed)
    elif isinstance(value, str):
        add(value)
    return dates


def choose_context(
    ancestors: list[tuple[str, Any]], max_chars: int = 7000
) -> tuple[str, Any]:
    for path, obj in reversed(ancestors):
        if not isinstance(obj, dict):
            continue
        try:
            size = len(json.dumps(obj, ensure_ascii=False))
        except TypeError:
            continue
        if size <= max_chars:
            return path, obj
    return ancestors[-1] if ancestors else ("$", {})


def find_matches(data: Any, query: str) -> list[Match]:
    needle = query.casefold().strip()
    matches: list[Match] = []
    seen: set[tuple[str, str]] = set()

    for path, text, ancestors in iter_strings(data):
        if needle not in text.casefold():
            continue
        key = (path, text)
        if key in seen:
            continue
        seen.add(key)

        context_path, context = choose_context(ancestors)
        dates: list[date] = []
        for source in (path, context_path):
            for parsed in parse_dates(source):
                if parsed not in dates:
                    dates.append(parsed)
        for parsed in collect_dates(context):
            if parsed not in dates:
                dates.append(parsed)
        if not dates:
            for ancestor_path, _ in reversed(ancestors):
                for parsed in parse_dates(ancestor_path):
                    if parsed not in dates:
                        dates.append(parsed)
                if dates:
                    break

        matches.append(
            Match(
                path=path,
                value=text,
                context_path=context_path,
                context=context,
                dates=sorted(dates),
            )
        )
    return matches


def in_semester(match: Match, start: date, end: date) -> bool | None:
    if not match.dates:
        return None
    return any(start <= item <= end for item in match.dates)


def flatten_scalars(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.extend(flatten_scalars(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(flatten_scalars(item, f"{prefix}[{index}]"))
    elif value is not None:
        text = str(value).strip()
        if text:
            result.append((prefix, text))
    return result


def first_by_key(
    items: list[tuple[str, str]],
    tokens: tuple[str, ...],
    *,
    reject: tuple[str, ...] = (),
) -> str | None:
    for key, value in items:
        low = key.casefold()
        if any(token in low for token in tokens) and not any(r in low for r in reject):
            return value
    return None


def find_time(items: list[tuple[str, str]]) -> str | None:
    preferred = first_by_key(
        items,
        ("time", "время", "begin", "start", "end"),
        reject=("timestamp",),
    )
    if preferred:
        match = TIME_RE.search(preferred)
        return match.group(0) if match else preferred
    for _, value in items:
        match = TIME_RE.search(value)
        if match:
            return match.group(0)
    return None


def compact_value(text: str, limit: int = 72) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def match_details(match: Match, query: str) -> list[str]:
    items = flatten_scalars(match.context)

    teacher = first_by_key(
        items,
        ("teacher", "educator", "lecturer", "препод", "teachername", "fio"),
    )
    subject = first_by_key(
        items,
        ("subject", "discipline", "lesson", "event_name", "title", "name", "дисцип"),
        reject=("teacher", "educator", "lecturer", "препод", "fio"),
    )
    room = first_by_key(
        items,
        ("room", "place", "auditor", "classroom", "location", "аудит"),
    )
    lesson_type = first_by_key(
        items,
        ("event_type", "lesson_type", "type", "вид", "занят"),
    )
    when = find_time(items)

    lines: list[str] = []
    if teacher and query.casefold() in teacher.casefold():
        lines.append(compact_value(teacher, 88))
    elif match.value:
        lines.append(compact_value(match.value, 88))

    secondary = [part for part in (subject, lesson_type) if part]
    if secondary:
        merged = " · ".join(dict.fromkeys(compact_value(x, 50) for x in secondary))
        if merged and merged.casefold() != lines[0].casefold():
            lines.append(merged)

    meta = [part for part in (when, room) if part]
    if meta:
        lines.append(" · ".join(dict.fromkeys(compact_value(x, 42) for x in meta)))

    return lines[:3]


def format_day(value: date) -> str:
    return f"{value.day:02d} {SHORT_MONTHS[value.month]} {value.year} · {WEEKDAYS[value.weekday()]}"


def format_period(start: date, end: date) -> str:
    return f"{start.day:02d}.{start.month:02d}.{start.year}–{end.day:02d}.{end.month:02d}.{end.year}"


def short_context(value: Any, limit: int = 2200) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n…"


def print_matches(
    ui: UI,
    selected: list[Match],
    query: str,
    *,
    verbose: bool,
) -> None:
    for index, match in enumerate(selected, 1):
        dates = match.dates or [None]
        relevant_date = dates[0]
        label = format_day(relevant_date) if relevant_date else "дата не распознана"

        print()
        print(ui.strong(label))
        details = match_details(match, query)
        for i, line in enumerate(details):
            prefix = "" if i == 0 else ui.muted("  ")
            print(f"{prefix}{line}")

        if verbose:
            print(ui.muted(f"  path: {match.path}"))
            print(ui.muted(f"  context: {match.context_path}"))
            context = short_context(match.context)
            for line in context.splitlines():
                print(ui.muted("  " + line))


def print_report(
    ui: UI,
    matches: list[Match],
    query: str,
    semester_start: date,
    semester_end: date,
    *,
    all_semesters: bool,
    verbose: bool,
) -> list[Match]:
    if all_semesters:
        selected = matches
    else:
        selected = [
            item
            for item in matches
            if in_semester(item, semester_start, semester_end) is True
        ]

    undated = [item for item in matches if in_semester(item, semester_start, semester_end) is None]
    outside = [item for item in matches if in_semester(item, semester_start, semester_end) is False]

    print()
    if selected:
        noun = "совпадение" if len(selected) == 1 else "совпадения"
        print(ui.ok(ui.strong(f"{query}: найдено {len(selected)} {noun}")))
        print_matches(ui, selected, query, verbose=verbose)
    elif matches and not all_semesters:
        print(ui.warn(ui.strong(f"{query}: в 3-м семестре не подтверждён")))
        print(
            ui.muted(
                f"В полном JSON есть {len(matches)} совпад., но их даты не попали в выбранный период."
            )
        )
    else:
        print(ui.bad(ui.strong(f"{query}: не найден")))

    if verbose and not all_semesters:
        print()
        ui.rule()
        ui.pair("всего", str(len(matches)))
        ui.pair("в семестре", str(len(selected)))
        ui.pair("без даты", str(len(undated)))
        ui.pair("вне", str(len(outside)))

        if undated:
            print()
            print(ui.warn("Совпадения без распознанной даты"))
            print_matches(ui, undated, query, verbose=True)

    return selected


def web_result_item(match: Match, query: str, start: date, end: date) -> dict[str, Any]:
    relevant = next((d for d in match.dates if start <= d <= end), None)
    details = match_details(match, query)
    title = details[0] if details else match.value
    return {
        "date": format_day(relevant) if relevant else "дата не распознана",
        "title": title,
        "details": details[1:],
        "context": match.context,
    }


def run_web_app(host: str, port: int, *, open_browser: bool = True) -> int:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            body = WEB_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/api/check":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                group = str(payload.get("group") or DEFAULT_GROUP).strip()
                query = str(payload.get("query") or DEFAULT_QUERY).strip()
                start = parse_iso_date(str(payload.get("start") or DEFAULT_SEMESTER_START.isoformat()))
                end = parse_iso_date(str(payload.get("end") or DEFAULT_SEMESTER_END.isoformat()))
                if start > end:
                    raise ValueError("Дата начала позже даты окончания")
                if not group or not query:
                    raise ValueError("Заполни группу и преподавателя")

                data = fetch_schedule(group)
                all_matches = find_matches(data, query)
                selected = [m for m in all_matches if in_semester(m, start, end) is True]

                output_dir = Path("mai_schedule_output")
                output_dir.mkdir(parents=True, exist_ok=True)
                save_json(output_dir / "schedule.json", data)
                save_json(
                    output_dir / "matches.json",
                    {
                        "group": group,
                        "query": query,
                        "source_url": build_schedule_url(group),
                        "semester_start": start.isoformat(),
                        "semester_end": end.isoformat(),
                        "total_matches": len(all_matches),
                        "selected_matches": len(selected),
                        "matches": [serialize_match(m) for m in all_matches],
                    },
                )

                self.send_json(
                    {
                        "group": group,
                        "query": query,
                        "period": format_period(start, end),
                        "count": len(selected),
                        "total": len(all_matches),
                        "matches": [web_result_item(m, query, start, end) for m in selected],
                    }
                )
            except argparse.ArgumentTypeError as exc:
                self.send_json({"error": str(exc)}, 400)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                message = "Расписание группы не найдено" if status == 404 else f"МАИ вернул HTTP {status}"
                self.send_json({"error": message}, 502)
            except requests.RequestException:
                self.send_json({"error": "Не удалось подключиться к public.mai.ru"}, 502)
            except (ValueError, OSError, json.JSONDecodeError, RuntimeError) as exc:
                self.send_json({"error": str(exc)}, 400)

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"МАИ / расписание  {url}")
    print("Ctrl+C — остановить")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


def serialize_match(match: Match) -> dict[str, Any]:
    return {
        "path": match.path,
        "value": match.value,
        "context_path": match.context_path,
        "dates": [item.isoformat() for item in match.dates],
        "context": match.context,
    }


def parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ожидается YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверить расписание МАИ на преподавателя или строку.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  python schedule.py\n"
            "  python schedule.py --cli --query Зверев\n"
            "  python schedule.py --input-json schedule.json\n"
            "  python schedule.py --verbose"
        ),
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="запустить старый терминальный режим вместо браузерного UI",
    )
    parser.add_argument("--host", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--group", default=DEFAULT_GROUP, help="группа МАИ")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="что искать")
    parser.add_argument(
        "--semester-start",
        type=parse_iso_date,
        default=DEFAULT_SEMESTER_START,
        help="начало периода, YYYY-MM-DD",
    )
    parser.add_argument(
        "--semester-end",
        type=parse_iso_date,
        default=DEFAULT_SEMESTER_END,
        help="конец периода, YYYY-MM-DD",
    )
    parser.add_argument(
        "--all-semesters",
        action="store_true",
        help="не фильтровать совпадения по 3-му семестру",
    )
    parser.add_argument(
        "--input-json",
        type=Path,
        help="читать уже скачанный JSON вместо сети",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("mai_schedule_output"),
        help="куда сохранить schedule.json и matches.json",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="показать JSON paths, контекст и диагностику",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="отключить ANSI-цвета",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    # Browser UI is the default human-facing mode. File/debug workflows stay CLI.
    if not args.cli and args.input_json is None and not args.verbose:
        return run_web_app(args.host, args.port, open_browser=not args.no_browser)

    ui = UI(color=not args.no_color)

    if args.semester_start > args.semester_end:
        ui.error("Некорректный период", "semester-start позже semester-end")
        return 2

    ui.heading(args.group, args.semester_start, args.semester_end)
    ui.pair("поиск", args.query)
    ui.pair("источник", str(args.input_json) if args.input_json else "public.mai.ru")

    try:
        if args.input_json:
            data = load_json(args.input_json)
        else:
            data = fetch_schedule(args.group)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 404:
            ui.error(
                "Расписание не найдено",
                f"МАИ вернул 404 для группы {args.group}.",
            )
        else:
            ui.error(f"HTTP {status}", str(exc))
        return 1
    except requests.RequestException as exc:
        ui.error(
            "Не удалось получить расписание",
            "Проверь сеть или передай локальный JSON: python schedule.py --input-json schedule.json",
        )
        if args.verbose:
            print(ui.muted(str(exc)), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        ui.error("Не удалось прочитать данные", str(exc))
        return 1

    matches = find_matches(data, args.query)
    selected = print_report(
        ui,
        matches,
        args.query,
        args.semester_start,
        args.semester_end,
        all_semesters=args.all_semesters,
        verbose=args.verbose,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = args.output_dir / "schedule.json"
    matches_path = args.output_dir / "matches.json"
    save_json(schedule_path, data)
    save_json(
        matches_path,
        {
            "group": args.group,
            "query": args.query,
            "source_url": build_schedule_url(args.group),
            "semester_start": args.semester_start.isoformat(),
            "semester_end": args.semester_end.isoformat(),
            "all_semesters": args.all_semesters,
            "total_matches": len(matches),
            "selected_matches": len(selected),
            "matches": [serialize_match(item) for item in matches],
        },
    )

    print()
    ui.rule()
    ui.pair("файлы", str(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
