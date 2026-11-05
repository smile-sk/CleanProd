#!/usr/bin/env python3
"""Сторож CleanTrade Payment API: молчание сервиса - это событие, а не норма.

Проверки:
  1. пульс поллера (data/poller_heartbeat.json) - старше 5 минут = детект стоит;
  2. цикл поллера упал с ошибкой - цитируем причину;
  3. в журнале доказательств появились ОТКАЗЫ записи (order_audit.failures.jsonl);
  4. заполнение диска > 85 %;
  5. живая проба сайта снаружи (healthz и защита админки) - 5xx и «не отвечает»;
  6. счётчик 5xx в access-логе: живые ошибки тех адресов, которые проба не дёргает.

Тревога уходит в Telegram админам (ADMIN_IDS) и всегда ложится в data/alerts.jsonl.
Повтор той же тревоги - не чаще раза в 30 минут, чтобы не спамить.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/root/CleanProd")
import config

DATA = config.DATA_DIR
HEARTBEAT = DATA / "poller_heartbeat.json"
FAILURES = DATA / "order_audit.failures.jsonl"
STATE = DATA / "watchdog_state.json"
ALERTS = DATA / "alerts.jsonl"
COOLDOWN = 1800
# Плановый рестарт: файл с меткой «до какого времени не авария». Сторож не
# должен будить оператора потому, что мы сами остановили сервис на правку.
# Метка живёт не дольше 30 минут - забыть и замаскировать настоящую аварию нельзя.
MAINT = DATA / "maintenance.until"
MAINT_MAX = 1800
STALE_SEC = 300
DISK_LIMIT = 85
# Живая проба: важно не «служба active», а что сайт ОТВЕЧАЕТ снаружи.
# (адрес, какие коды считаем нормой, метка)
PROBES = [
    ("https://pay.cleantrade.info/healthz", (200,), "healthz"),
    ("https://pay.cleantrade.info/admin", (401, 403), "admin"),
]
PROBE_TIMEOUT = 10
APACHE_ACCESS = "/var/log/apache2/other_vhosts_access.log"
VHOST_FILTER = "pay.cleantrade.info"


def now():
    return datetime.now(timezone.utc)


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    try:
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(STATE))
    except Exception as exc:
        print("state save failed:", exc)


def unit_state() -> str:
    """Состояние самой службы - чтобы «пульса нет» не расшифровывать задним числом.

    Файла нет по двум совсем разным причинам: служба остановлена (наши работы,
    падение) или служба живёт, но детект не стартовал. В тревоге должно быть
    видно, что именно, иначе разбор начинается с нуля.
    """
    try:
        out = subprocess.run(["systemctl", "is-active", "stablepay"],
                             capture_output=True, text=True, timeout=5)
        return (out.stdout or out.stderr or "?").strip() or "?"
    except Exception as exc:
        return "неизвестно ({})".format(type(exc).__name__)


def maintenance_active() -> bool:
    """Идёт ли плановое окно (файл data/maintenance.until с меткой эпохи).

    Самоистекает: даже забытая метка перестаёт действовать через 30 минут,
    поэтому «заглушить» сторожа навсегда ею нельзя.
    """
    try:
        until = float(MAINT.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return False
    return now().timestamp() < min(until, now().timestamp() + MAINT_MAX)


def notify(text):
    """Telegram админам. Не смогли - тревога всё равно останется в файле."""
    token = getattr(config, "SUPPORT_BOT_TOKEN", "") or ""
    ids = getattr(config, "ADMIN_IDS", []) or []
    sent = []
    if token and ids:
        for chat_id in ids:
            try:
                data = urllib.parse.urlencode({
                    "chat_id": str(chat_id), "text": text,
                    "disable_web_page_preview": "true"}).encode()
                req = urllib.request.Request(
                    "https://api.telegram.org/bot{}/sendMessage".format(token), data=data)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    sent.append((chat_id, resp.status))
            except Exception as exc:
                sent.append((chat_id, "ERR {}".format(exc)))
    try:
        with ALERTS.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": now().isoformat(timespec="seconds"),
                                 "text": text, "tg": sent}, ensure_ascii=False) + "\n")
    except Exception as exc:
        print("alerts write failed:", exc)
    return sent


def http_get(url, timeout=PROBE_TIMEOUT):
    """GET снаружи. Возвращает (код, тело); код 0 = не достучались."""
    req = urllib.request.Request(url, headers={"User-Agent": "CleanTrade-Watchdog/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(3000)
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except Exception as exc:
        return 0, "{}: {}".format(type(exc).__name__, exc).encode("utf-8")


def five_xx_count():
    """Сколько 5xx отдал сайт за всё время жизни лога.

    Ловит ошибки на тех адресах, которые проба не дёргает: /v1/orders,
    вебхуки, страницы. Сравниваем с прошлым значением - важен прирост.
    -1 = лог недоступен (не повод молчать, но и не 5xx).
    """
    total = 0
    try:
        with open(APACHE_ACCESS, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if VHOST_FILTER not in line:
                    continue
                parts = line.split('"')
                if len(parts) < 3:
                    continue
                tail = parts[2].split()
                if tail and tail[0][:1] == "5":
                    total += 1
    except Exception:
        return -1
    return total


def main():
    state = load_state()
    problems = []   # [(код, текст)]: код стабилен для дедупликации, текст - людям

    hb = {}
    try:
        hb = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
    except Exception:
        problems.append(("pulse_missing",
                         "ПУЛЬСА НЕТ: файл {} не читается (служба stablepay: {}) - "
                         "поллер не запускался или сервис не работает".format(
                             HEARTBEAT.name, unit_state())))
    if hb:
        age = now().timestamp() - float(hb.get("ts_epoch") or 0)
        if age > STALE_SEC:
            problems.append(("pulse_stale",
                             "ДЕТЕКТ СТОИТ: последний цикл {:.0f} мин назад "
                             "(порог {} мин)".format(age / 60.0, STALE_SEC / 60)))
        if not hb.get("ok", True):
            problems.append(("cycle_failed",
                             "ЦИКЛ УПАЛ: {}".format(hb.get("error") or "причина не записана")))

    fails = 0
    try:
        if FAILURES.exists():
            fails = len(FAILURES.read_text(encoding="utf-8").splitlines())
    except Exception:
        pass
    if fails > int(state.get("failures", 0)):
        problems.append(("audit_failures",
                         "ЖУРНАЛ ДОКАЗАТЕЛЬСТВ НЕ ПИШЕТСЯ: отказов {} (было {}) - "
                         "смотри {}".format(fails, state.get("failures", 0), FAILURES)))
    state["failures"] = fails

    total, used, free = shutil.disk_usage("/")
    pct = used * 100.0 / total
    if pct > DISK_LIMIT:
        problems.append(("disk_full",
                         "ДИСК: занято {:.0f}% (порог {}%)".format(pct, DISK_LIMIT)))
    state["disk_pct"] = round(pct, 1)

    # 5. Живая проба наружу: сайт должен отвечать телом, а не только «служба активна».
    for url, expect, label in PROBES:
        code, body = http_get(url)
        if code == 0:
            problems.append(("http_down",
                             "САЙТ НЕ ОТВЕЧАЕТ: {} - {} (служба stablepay: {})".format(
                                 label, body.decode("utf-8", "replace")[:120], unit_state())))
        elif code >= 500:
            problems.append(("http_5xx",
                             "ПРИЛОЖЕНИЕ ОТДАЁТ {} на {} (служба stablepay: {})".format(
                                 code, label, unit_state())))
        elif code not in expect:
            problems.append(("http_bad",
                             "НЕОЖИДАННЫЙ ОТВЕТ {} на {} (ждали {})".format(
                                 code, label, "/".join(str(x) for x in expect))))
        elif label == "healthz" and b'"ok":true' not in body.replace(b" ", b""):
            problems.append(("http_bad",
                             "HEALTHZ ОТВЕЧАЕТ НЕ OK: {}".format(
                                 body.decode("utf-8", "replace")[:120])))

    # 6. Прирост 5xx в access-логе.
    five = five_xx_count()
    prev_five = int(state.get("http5xx", 0))
    if five >= 0 and five > prev_five:
        problems.append(("http_5xx_log",
                         "САЙТ ОТДАЛ {} ОШИБОК 5xx (было {}) - смотри {}".format(
                             five, prev_five, APACHE_ACCESS)))
    if five >= 0:
        state["http5xx"] = five

    sent_any = []
    maint = maintenance_active()
    for code, text in problems:
        if maint and code in ("pulse_missing", "pulse_stale"):
            # Мы сами остановили сервис на правку: это не авария. В выводе
            # остаётся, в Telegram не уходит.
            continue
        key = "k:" + code
        last = float(state.get(key) or 0)
        if now().timestamp() - last < COOLDOWN:
            continue
        line = "[CleanTrade {}] {}".format(now().strftime("%d.%m %H:%M UTC"), text)
        sent_any.append(notify(line))
        state[key] = now().timestamp()

    save_state(state)
    print(json.dumps({"checked": now().isoformat(timespec="seconds"),
                      "problems": [text for _, text in problems],
                      "codes": [code for code, _ in problems],
                      "maintenance": maint, "alerts_sent": sent_any,
                      "failures": fails, "disk_pct": round(pct, 1), "http5xx": five,
                      "heartbeat": hb}, ensure_ascii=False))


if __name__ == "__main__":
    main()
