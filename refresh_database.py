#!/usr/bin/env python3
"""Build a complete current-semester MAI schedule database.

The script uses the public MAI group index and downloads every group's JSON
schedule, keeps only the requested semester, then reuses the project's hybrid
compressor to build database_v413.json and a file:// friendly JS wrapper.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
GROUPS_URL = "https://public.mai.ru/schedule/data/groups.json"
DATA_BASE_URL = "https://public.mai.ru/schedule/data"
DEFAULT_START = date(2026, 8, 31)
DEFAULT_END = date(2027, 1, 31)
DB_PATH = ROOT / "database_v413.json"
DB_JS_PATH = ROOT / "database_v413.js"
STATUS_PATH = ROOT / "database_status.json"
BUILD_DIR = ROOT / "schedules" / "current"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )
}
_thread_local = threading.local()


@dataclass(slots=True)
class FetchResult:
    group: str
    state: str
    path: Path | None = None
    error: str | None = None


def _session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        _thread_local.session = session
    return session


def _request_json(url: str, *, timeout: float, retries: int = 2) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = _session().get(url, timeout=timeout)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
    raise RuntimeError(str(last_error) if last_error else "unknown request error")


def _parse_date_key(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


def _extract_group_names(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise ValueError("groups.json имеет неожиданный формат: ожидался список")
    names: set[str] = set()
    for item in payload:
        if isinstance(item, dict):
            name = item.get("name")
        elif isinstance(item, str):
            name = item
        else:
            name = None
        if isinstance(name, str) and name.strip():
            names.add(name.strip().upper())
    if not names:
        raise ValueError("groups.json не содержит групп")
    return sorted(names)


def _filter_schedule(payload: Any, group: str, start: date, end: date) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    result: dict[str, Any] = {"group": str(payload.get("group") or group).strip().upper()}
    for key, value in payload.items():
        day = _parse_date_key(key)
        if day is None or not (start <= day <= end):
            continue
        if isinstance(value, dict):
            result[key] = value
    return result if len(result) > 1 else None


def _fetch_group(
    group: str,
    *,
    start: date,
    end: date,
    timeout: float,
    base_url: str,
    target_dir: Path,
) -> FetchResult:
    digest = hashlib.md5(group.encode("utf-8")).hexdigest()
    url = f"{base_url.rstrip('/')}/{digest}.json"
    try:
        payload = _request_json(url, timeout=timeout)
        if payload is None:
            return FetchResult(group, "not_found")
        filtered = _filter_schedule(payload, group, start, end)
        if filtered is None:
            return FetchResult(group, "empty")
        target = target_dir / f"{digest}.json"
        target.write_text(json.dumps(filtered, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return FetchResult(group, "ok", target)
    except Exception as exc:  # keep a single bad group from aborting the entire rebuild
        return FetchResult(group, "error", error=str(exc))


def _write_js_wrapper(database: dict[str, Any]) -> None:
    payload = json.dumps(database, ensure_ascii=False, separators=(",", ":"))
    DB_JS_PATH.write_text(f"window.__MAI_BUNDLED_DATABASE__ = {payload};\n", encoding="utf-8")


def _write_status(**fields: Any) -> None:
    STATUS_PATH.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")


def database_is_complete(start: date, end: date) -> bool:
    # The JSON database is the source of truth. The JS wrapper is derived and can
    # always be regenerated, so its absence/staleness must never trigger a full
    # 919-group network rebuild.
    if not DB_PATH.exists() or not STATUS_PATH.exists():
        return False
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        database = json.loads(DB_PATH.read_text(encoding="utf-8"))
        info = database.get("build_info") or {}
        groups = database.get("dictionaries", {}).get("groups") or []
        group_count = max(0, len(groups) - 1)
        return bool(
            status.get("complete") is True
            and status.get("semester_start") == start.isoformat()
            and status.get("semester_end") == end.isoformat()
            and str(database.get("version") or "") == "413"
            and info.get("complete") is True
            and info.get("semester_start") == start.isoformat()
            and info.get("semester_end") == end.isoformat()
            and group_count >= 100
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def build_database(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        raise ValueError("Дата начала позже даты окончания")

    if args.if_needed and database_is_complete(start, end):
        # Keep the file:// wrapper in sync even when no network rebuild is needed.
        # This repairs stale/null database_v413.js left from an earlier packaged build.
        database = json.loads(DB_PATH.read_text(encoding="utf-8"))
        _write_js_wrapper(database)
        print("База уже полная и соответствует выбранному семестру.")
        print("JS-обёртка синхронизирована с database_v413.json.")
        return 0

    print("Получаю список групп МАИ…")
    groups_payload = _request_json(args.groups_url, timeout=args.timeout)
    groups = _extract_group_names(groups_payload)
    print(f"Найдено групп: {len(groups)}")

    temp_dir = BUILD_DIR.with_name("current.tmp")
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    ok = empty = not_found = errors = 0
    failed: list[dict[str, str]] = []
    print(f"Скачиваю расписания ({args.workers} потоков)…")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                _fetch_group,
                group,
                start=start,
                end=end,
                timeout=args.timeout,
                base_url=args.data_base_url,
                target_dir=temp_dir,
            ): group
            for group in groups
        }
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            result = future.result()
            done += 1
            if result.state == "ok":
                ok += 1
            elif result.state == "empty":
                empty += 1
            elif result.state == "not_found":
                not_found += 1
            else:
                errors += 1
                failed.append({"group": result.group, "error": result.error or "unknown"})
            if done == total or done % 50 == 0:
                print(f"  {done}/{total} · с расписанием {ok} · пусто {empty} · отсутствует {not_found} · ошибок {errors}")

    if ok == 0:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("Не удалось получить ни одного расписания текущего семестра")

    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    temp_dir.replace(BUILD_DIR)

    print("Сжимаю полную базу…")
    import main  # imported only after BUILD_DIR has been populated

    main.LOCAL_SCHEDULES_DIR = BUILD_DIR
    main.COMPRESSED_SCHEDULE_FILE = DB_PATH
    main.create_compressed_schedule_database()
    if not DB_PATH.exists():
        raise RuntimeError("Компрессор не создал database_v413.json")

    database = json.loads(DB_PATH.read_text(encoding="utf-8"))
    group_count = max(0, len(database.get("dictionaries", {}).get("groups", [])) - 1)
    database["version"] = "413"
    database["build_info"] = {
        "complete": errors == 0,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "semester_start": start.isoformat(),
        "semester_end": end.isoformat(),
        "groups_indexed": len(groups),
        "groups_with_lessons": ok,
        "groups_in_database": group_count,
        "download_errors": errors,
    }
    DB_PATH.write_text(json.dumps(database, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    _write_js_wrapper(database)

    complete = errors == 0 and group_count >= 100
    _write_status(
        version="4.1.5",
        complete=complete,
        generated_at=database["build_info"]["generated_at"],
        semester_start=start.isoformat(),
        semester_end=end.isoformat(),
        groups_indexed=len(groups),
        groups_with_lessons=ok,
        groups_in_database=group_count,
        empty_groups=empty,
        missing_groups=not_found,
        download_errors=errors,
        failed=failed[:100],
    )

    if not args.keep_raw:
        shutil.rmtree(BUILD_DIR, ignore_errors=True)

    print()
    print(f"Готово: {DB_PATH.name}")
    print(f"Групп в базе: {group_count}")
    print(f"Размер: {DB_PATH.stat().st_size / 1024 / 1024:.2f} МБ")
    if errors:
        print(f"Предупреждение: {errors} групп не скачались. Можно запустить обновление ещё раз.")
    return 0 if complete else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Скачать все группы МАИ и собрать полную локальную базу")
    p.add_argument("--start", default=DEFAULT_START.isoformat())
    p.add_argument("--end", default=DEFAULT_END.isoformat())
    p.add_argument("--workers", type=int, default=min(24, (os.cpu_count() or 4) * 4))
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--groups-url", default=GROUPS_URL)
    p.add_argument("--data-base-url", default=DATA_BASE_URL)
    p.add_argument("--keep-raw", action="store_true")
    p.add_argument("--if-needed", action="store_true")
    return p


if __name__ == "__main__":
    try:
        raise SystemExit(build_database(parser().parse_args()))
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        raise SystemExit(130)
    except Exception as exc:
        print(f"Ошибка обновления базы: {exc}", file=sys.stderr)
        raise SystemExit(1)
