"""Внешний рубеж API: блок-лист адресов и лимит частоты запросов.

Написано 18.09.2026 после суточного прогона по платёжке (example-ip и сеть
x:x:x::/64). До этого в API не было ни того, ни другого, поэтому
прошли: 37 646 попыток подписки, 1 015 попыток подбора админ-ключа, 1 980
попыток подобрать секрет поддержки и 41 699 вызовов легаси-пути без Telegram.

Блок-лист - файл data/blocklist.json: {"blocked": ["1.2.3.4", "2602:...::/64"]}.
Перечитывается по mtime, поэтому новый адрес вступает в силу без рестарта.
Лимит - скользящее окно в памяти процесса (без внешних зависимостей).
"""
import ipaddress
import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional, Tuple

import config

LOCK = threading.RLock()
_BLOCK = {"mtime": None, "nets": ()}
_HITS = {}


def _parse_net(value):
    try:
        return ipaddress.ip_network(str(value).strip(), strict=False)
    except ValueError:
        return None


def _parse_addr(value):
    try:
        return ipaddress.ip_address(str(value or "").strip().strip("[]"))
    except ValueError:
        return None


def blocked(ip: str) -> Optional[str]:
    """Адрес (или его сеть) в блок-листе -> вернуть совпавшее правило."""
    addr = _parse_addr(ip)
    if addr is None:
        return None
    path = Path(str(config.BLOCKLIST_FILE))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    with LOCK:
        if _BLOCK["mtime"] != mtime:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                raw = {}
            items = raw.get("blocked") if isinstance(raw, dict) else raw
            nets = tuple(n for n in (_parse_net(x) for x in (items or [])) if n)
            _BLOCK.update({"mtime": mtime, "nets": nets})
        nets = _BLOCK["nets"]
    for net in nets:
        if addr in net:
            return str(net)
    return None


def allow(ip: str, bucket: str, limit: int, window: float) -> Tuple[bool, int]:
    """Скользящее окно на адрес: (можно ли, сколько запросов уже было в окне)."""
    if not ip or limit <= 0:
        return True, 0
    now = time.monotonic()
    with LOCK:
        queue = _HITS.get((ip, bucket))
        if queue is None:
            queue = _HITS[(ip, bucket)] = deque()
        while queue and now - queue[0] > window:
            queue.popleft()
        if len(queue) >= limit:
            return False, len(queue)
        queue.append(now)
        if len(_HITS) > 20000:   # ротация адресов не должна растить память
            for key in [k for k, v in _HITS.items() if not v or now - v[-1] > 3600]:
                _HITS.pop(key, None)
    return True, 0
