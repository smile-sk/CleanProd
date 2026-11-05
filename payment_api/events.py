"""Лента событий CleanTrade Payment API (админка и аудит).

Каждое событие пишется в кольцевой буфер (для /admin/api/events) и в файл
data/events.jsonl (история, переживает рестарт). Формат строки:
    {"ts": "...", "kind": "...", "data": {...}}

События: order_created, order_paid, order_expired, plan_activated,
webhook_ok, webhook_fail, auth_fail, limit_reached, poller_error.
"""
import json
import sys
import threading
from collections import deque
from datetime import datetime, timezone

import config

LOCK = threading.RLock()
BUFFER = deque(maxlen=200)
EVENTS_PATH = config.DATA_DIR / "events.jsonl"


def log_event(kind: str, **data) -> None:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "data": data,
    }
    with LOCK:
        BUFFER.append(event)
        try:
            with EVENTS_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:
            sys.stderr.write("EVENTS WRITE FAILED [{}: {}] kind={}\n".format(
                type(exc).__name__, exc, kind))
            sys.stderr.flush()


def recent(limit: int = 50):
    """Свежие события из буфера (память процесса)."""
    with LOCK:
        events = list(BUFFER)
    return events[-limit:][::-1]


def history(limit: int = 200):
    """Последние события из файла (переживают рестарт сервиса)."""
    if not EVENTS_PATH.exists():
        return []
    try:
        lines = EVENTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    except Exception:
        return []
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events[::-1]


_COUNTERS_CACHE = {"key": None, "value": None}


def counters():
    """Счётчики по ВСЕМУ журналу событий, а не по последним N.

    До 26.10.2026 админка считала вебхуки и отказы авторизации по последним
    100 событиям и показывала результат как итог: «отказов 13» при 1095 в
    журнале. Срез выдавал себя за факт - для доказательств это недопустимо.
    Файл читается построчно, битые строки пропускаются, но их отказ виден
    в stderr. Результат кэшируется по (mtime, size): админку можно обновлять.
    """
    empty = {"total": 0, "kinds": {}, "first_ts": None, "last_ts": None, "24h": {}}
    if not EVENTS_PATH.exists():
        return empty
    try:
        st = EVENTS_PATH.stat()
        key = (st.st_mtime_ns, st.st_size)
    except Exception:
        key = None
    with LOCK:
        if key is not None and _COUNTERS_CACHE["key"] == key:
            return _COUNTERS_CACHE["value"]
    total = 0
    kinds = {}
    day = {}
    first_ts = None
    last_ts = None
    cutoff = datetime.now(timezone.utc).timestamp() - 86400
    try:
        with EVENTS_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                kind = ev.get("kind") or "?"
                total += 1
                kinds[kind] = kinds.get(kind, 0) + 1
                ts = ev.get("ts") or ""
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts
                    try:
                        if datetime.fromisoformat(ts).timestamp() >= cutoff:
                            day[kind] = day.get(kind, 0) + 1
                    except Exception:
                        pass
    except Exception as exc:
        sys.stderr.write("EVENTS COUNTERS FAILED [{}: {}]\n".format(
            type(exc).__name__, exc))
        sys.stderr.flush()
        return empty
    value = {"total": total, "kinds": kinds, "first_ts": first_ts,
             "last_ts": last_ts, "24h": day}
    with LOCK:
        _COUNTERS_CACHE["key"] = key
        _COUNTERS_CACHE["value"] = value
    return value
