"""Журнал аудита заказов CleanTrade - доказательная база, а не «мы посмотрели».

Зачем это нужно. Клиент может заявить: «я продал товар за $1000, а из-за вашего
сервиса денег не получил - теперь вы мне должны». Отвечать словами тут нельзя,
нужна запись с фактами:

  * когда заказ создан, на какой кошелёк, на какую сумму;
  * сколько раз и в какое время мы проверяли этот кошелёк в блокчейне;
  * был ли перевод нужной суммы после создания заказа (tx_id, время блока);
  * что именно ответил сервер мерчанта на вебхук и сколько было попыток;
  * если перевода не было - так и написано, с окном проверки и ссылкой,
    где любой человек может проверить это независимо (блокчейн публичный).

Файл `data/order_audit.jsonl` - append-only: строки только ДОБАВЛЯЮТСЯ и никогда
не переписываются. Формат строки:

    {"ts": "...", "order_id": "...", "kind": "...", ...поля}

Виды записей (kind):

    created     - заказ создан (кошелёк, сумма, продукт, IP, адрес вебхука)
    scan        - кошелёк проверен в блокчейне (сколько ждём, сколько видно)
    scan_error  - оба канала не ответили (сеть/лимит): доказательство, что мы
                  пытались, а не молчали
    matched     - найден перевод: tx_id, сумма, время блока
    paid        - статус заказа сменён на paid
    paid_late   - оплата после срока, заказ оживлён
    expired     - заказ закрыт по сроку (итог: перевода не было)
    webhook     - попытка доставки вебхука (код ответа, длительность)

Записи `scan` пишутся не чаще, чем раз в SCAN_HEARTBEAT секунд на кошелёк:
поллер ходит каждые 30 с, и хранить каждый проход незачем - для доказательства
достаточно «проверяли с такого-то по такое-то, N раз».
"""
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import config

LOCK = threading.RLock()
AUDIT_PATH = config.DATA_DIR / "order_audit.jsonl"

# Как часто писать «кошелёк проверен» по одному и тому же адресу.
SCAN_HEARTBEAT = 600.0

# Сколько строк максимум читать с конца файла (защита от роста файла).
MAX_READ_LINES = 200_000

_heartbeat: Dict[str, tuple] = {}   # wallet -> (last_written_monotonic, scans, errors)

# Русские подписи для админки: страница аудита должна читаться без словаря.
KIND_RU = {
    "webhook_giveup": "вебхук не доставлен",
    "created": "заказ создан",
    "scan": "проверка кошелька",
    "scan_error": "канал не ответил",
    "matched": "перевод найден",
    "paid": "заказ оплачен",
    "paid_late": "оплата после срока",
    "expired": "заказ истёк",
    "webhook": "вебхук отправлен",
}

FIELDS_RU = {
    "wallet": "кошелёк",
    "amount": "сумма",
    "product_id": "товар",
    "customer_ref": "метка клиента",
    "webhook_url": "адрес вебхука",
    "ip": "IP",
    "pending": "ждали оплаты",
    "transfers": "переводов в ответе",
    "source": "канал",
    "scans": "проходов",
    "since": "период записи, с",
    "error": "ошибка",
    "tx_id": "tx",
    "expected_amount": "ждали",
    "tx_amount": "пришло",
    "tx_ts": "время блока",
    "delay_s": "через, с",
    "late": "поздняя оплата",
    "paid_at": "оплачено (UTC)",
    "reason": "причина",
    "created_at": "создан (UTC)",
    "attempt": "попытка",
    "ok": "доставлен",
    "status": "код ответа",
    "url": "адрес",
    "duration_ms": "длительность, мс",
}

# Служебные коды в значениях - тоже по-русски.
VALUES_RU = {
    "timeout": "истёк срок",
    "grace": "догон после срока",
    "admin_cancel": "отменён в админке",
    "tronscan": "TronScan",
    "trongrid": "TronGrid",
}


def kind_ru(kind: str) -> str:
    """Служебный код записи журнала → человеческое название для админки."""
    key = (kind or "").strip()
    return KIND_RU.get(key, key or "-")


def describe(record: dict) -> str:
    """Запись журнала → строка по-русски: «кошелёк: …; сумма: …»."""
    parts = []
    for key, value in (record or {}).items():
        if key in ("ts", "kind", "order_id"):
            continue
        label = FIELDS_RU.get(key, key)
        if isinstance(value, bool):
            text = "да" if value else "нет"
        elif value is None or value == "":
            text = "-"
        else:
            text = str(value)
            text = VALUES_RU.get(text, text)
        parts.append("{}: {}".format(label, text))
    return "; ".join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


FAILURES_PATH = config.DATA_DIR / "order_audit.failures.jsonl"
WRITE_FAILURES = 0
LAST_WRITE_ERROR = ""


def _record_failure(line: str, exc: Exception) -> None:
    """Журнал доказательств обязан уметь сказать «я не записал».

    Молчаливый pass здесь - худший вариант из возможных: заказ обработан,
    доказательства нет, и никто об этом не знает. Поэтому отказ идёт в
    stderr (journal), в отдельный файл отказов и в счётчик для админки.
    """
    global WRITE_FAILURES, LAST_WRITE_ERROR
    # Обработчик отказа сам не имеет права упасть: иначе журнал, который должен
    # защищать заказ, начнёт его ломать. Поэтому глушим всё, кроме счётчика.
    try:
        WRITE_FAILURES += 1
        LAST_WRITE_ERROR = "{}: {}".format(type(exc).__name__, exc)[:300]
        try:
            sys.stderr.write("AUDIT WRITE FAILED [{}]: {}\n".format(
                LAST_WRITE_ERROR, line[:200]))
            sys.stderr.flush()
        except Exception:
            pass
        try:
            with FAILURES_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": _now(), "error": LAST_WRITE_ERROR,
                                     "record": line[:1000]}, ensure_ascii=False) + "\n")
        except Exception:
            pass
    except Exception:
        pass


def log(order_id: str, kind: str, **fields) -> None:
    """Добавить запись в журнал. Ошибка записи не ломает заказ, но видна."""
    record = {"ts": _now(), "order_id": order_id or "", "kind": kind}
    record.update(fields)
    line = json.dumps(record, ensure_ascii=False, default=str)
    with LOCK:
        try:
            with AUDIT_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            _record_failure(line, exc)


def scan(wallet: str, pending: int = 0, transfers: int = 0,
         source: str = "", force: bool = False) -> None:
    """Отметить проверку кошелька - не чаще SCAN_HEARTBEAT секунд на адрес.

    Между записями копим счётчик: в следующей записи видно, сколько проходов
    было на самом деле, а не сколько строк в журнале.
    """
    wallet = (wallet or "").strip()
    if not wallet:
        return
    now = time.monotonic()
    with LOCK:
        last, scans, errors = _heartbeat.get(wallet, (0.0, 0, 0))
        scans += 1
        if not force and last and (now - last) < SCAN_HEARTBEAT:
            _heartbeat[wallet] = (last, scans, errors)
            return
        _heartbeat[wallet] = (now, 0, errors)
    log("", "scan", wallet=wallet, pending=pending, transfers=transfers,
        source=source, scans=scans, since=SCAN_HEARTBEAT)


def scan_error(wallet: str, error: str = "", pending: int = 0) -> None:
    """Оба канала не ответили: записываем каждую неудачу (их немного)."""
    log("", "scan_error", wallet=(wallet or "").strip(), error=error[:300],
        pending=pending)


def _read_lines() -> List[str]:
    if not AUDIT_PATH.exists():
        return []
    try:
        with AUDIT_PATH.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    return lines[-MAX_READ_LINES:]


def timeline(order_id: str, limit: int = 500) -> List[dict]:
    """Все записи по одному заказу, старые сверху."""
    out = []
    for line in _read_lines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("order_id") == order_id:
            out.append(rec)
    return out[-limit:]


def scan_count(wallet: str, since_ts: float, until_ts: Optional[float] = None) -> int:
    """Сколько раз проверяли кошелёк в окне (по записям scan/scan_error)."""
    until_ts = until_ts or time.time()
    total = 0
    for line in _read_lines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") == "scan_error" and rec.get("wallet") == wallet:
            total += 1
            continue
        if rec.get("kind") != "scan" or rec.get("wallet") != wallet:
            continue
        try:
            ts = datetime.fromisoformat(rec["ts"]).timestamp()
        except Exception:
            continue
        if since_ts <= ts <= until_ts:
            total += max(1, int(rec.get("scans") or 1))
    return total


def last_scan(wallet: str) -> str:
    """Время последней проверки кошелька (UTC, ISO) или пустая строка."""
    best = ""
    for line in _read_lines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") in ("scan", "scan_error") and rec.get("wallet") == wallet:
            best = rec.get("ts") or best
    return best


def evidence(order: dict) -> dict:
    """Собрать доказательство по заказу: запись + вердикт + ссылка для проверки.

    Возвращает словарь, который отдаётся в /admin/api/orders/<id>/audit и
    печатается в админке - то, что можно показать клиенту с претензией.
    """
    order = order or {}
    order_id = order.get("id") or ""
    wallet = order.get("wallet") or ""
    amount = order.get("amount")
    created = order.get("created_at") or ""
    status = order.get("status") or ""

    try:
        created_ts = datetime.fromisoformat(created).timestamp()
    except Exception:
        created_ts = 0.0
    ttl = config.ORDER_EXPIRATION_SECONDS
    expires = ""
    if created_ts:
        expires = datetime.fromtimestamp(created_ts + ttl, timezone.utc) \
            .isoformat(timespec="seconds")

    checks = scan_count(wallet, created_ts) if created_ts else 0
    # В карточке показываем последнюю проверку ТОЛЬКО внутри окна заказа:
    # время до создания заказа к доказательству не относится и путает.
    last_checked = last_scan(wallet) if wallet else ""
    if created and last_checked and last_checked < created:
        last_checked = ""
    records = timeline(order_id, limit=1000)
    matched = [r for r in records if r.get("kind") in ("matched", "paid", "paid_late")]
    hooks = [r for r in records if r.get("kind") == "webhook"]

    if matched:
        verdict = ("Оплата найдена в блокчейне: перевод {tx} на {amt} USDT в кошелёк "
                   "{wallet}, время блока {ts}; заказ {oid} отмечен оплаченным."
                   ).format(tx=matched[0].get("tx_id") or order.get("tx_id") or "?",
                            amt=amount, wallet=wallet, ts=matched[0].get("tx_ts") or "?",
                            oid=order_id)
    elif status == "expired":
        verdict = ("За время заказа совпадающего перевода не было. Ожидали входящий "
                   "перевод USDT TRC20 на {amt} USDT в кошелёк {wallet} со временем "
                   "блока позже {created} UTC и раньше {expires} UTC. Детектор проверил "
                   "кошелёк {checks} раз - совпадений нет, вебхук не полагался.").format(
                       amt=amount, wallet=wallet, created=created,
                       expires=expires or "expiry", checks=checks)
    else:
        verdict = ("Заказ ещё ждёт оплаты. Ожидаем входящий перевод USDT TRC20 "
                   "на {amt} USDT в кошелёк {wallet} позже {created} UTC; кошелёк "
                   "проверен {checks} раз.").format(
                       amt=amount, wallet=wallet, created=created, checks=checks)

    return {
        "order": order,
        "expected": {
            "wallet": wallet,
            "amount": amount,
            "since_utc": created,
            "until_utc": expires,
            "network": "USDT TRC20",
            "rule": ("входящий перевод на кошелёк заказа, сумма равна сумме заказа "
                     "(допуск 0.005), время блока позже создания заказа, "
                     "tx_id не занят другим заказом"),
        },
        "checks": {"scan_records": checks, "last_scan_utc": last_checked},
        "webhook_attempts": hooks,
        "verdict": verdict,
        "verify_url": "https://tronscan.org/#/address/{}".format(wallet) if wallet else "",
        "timeline": records,
    }


def stats(days: int = 14) -> dict:
    """Счётчики по дням: заказы созданы/оплачены/истекли, вебхуки, проверки.

    Это и есть «наша успешность через нас проходящего»: видно, сколько заказов
    было, сколько закрылось оплатой, сколько истекло и как ведут себя вебхуки.
    """
    days = max(1, min(int(days), 90))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    buckets: Dict[str, dict] = {}

    def bucket(day: str) -> dict:
        return buckets.setdefault(day, {
            "created": 0, "paid": 0, "paid_late": 0, "expired": 0,
            "webhook_ok": 0, "webhook_fail": 0, "scans": 0, "scan_errors": 0,
        })

    for line in _read_lines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        try:
            ts = datetime.fromisoformat(rec.get("ts") or "")
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < since:
            continue
        b = bucket(ts.date().isoformat())
        kind = rec.get("kind")
        if kind in ("created", "paid", "paid_late", "expired"):
            b[kind] += 1
        elif kind == "webhook":
            b["webhook_ok" if rec.get("ok") else "webhook_fail"] += 1
        elif kind == "scan":
            b["scans"] += max(1, int(rec.get("scans") or 1))
        elif kind == "scan_error":
            b["scan_errors"] += 1

    today = datetime.now(timezone.utc).date()
    out = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        row = bucket(day) if day in buckets else bucket(day)
        row["day"] = day
        out.append(row)
    return {"days": out, "total": _totals(out)}


def _totals(rows: List[dict]) -> dict:
    total = {"created": 0, "paid": 0, "paid_late": 0, "expired": 0,
             "webhook_ok": 0, "webhook_fail": 0, "scans": 0, "scan_errors": 0}
    for row in rows:
        for key in total:
            total[key] += int(row.get(key) or 0)
    created = total["created"] or 1
    total["paid_share"] = round(100.0 * total["paid"] / created, 1)
    hooks = total["webhook_ok"] + total["webhook_fail"]
    total["webhook_share"] = round(100.0 * total["webhook_ok"] / hooks, 1) if hooks else 0.0
    return total
