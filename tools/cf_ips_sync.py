#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cf_ips_sync.py - сторож списка Cloudflare для origin-only гейта Apache.

Что делает: раз в час тянет официальный список диапазонов Cloudflare
(https://api.cloudflare.com/client/v4/ips), сверяет с сохранённым etag и, если
Cloudflare что-то поменял, переписывает /etc/apache2/cody-origin-only.inc.

Порядок применения - с откатом: сначала бэкап текущего файла, потом запись,
потом `apachectl configtest`, потом reload, потом ЖИВАЯ проба (curl с нашего
127.0.0.1 - гейт обязан нас пропустить). Любой шаг не прошёл - файл
восстанавливается из бэкапа, Apache перезагружается со старым списком.
Мёртвым (то есть закрытым для всех) гейт не остаётся никогда.

НАШИ адреса (OUR_IPS) лежат в этом файле отдельно от диапазонов Cloudflare и
никогда не зависят от сети: даже если список CF устареет, вход на origin для
нас сохраняется. Добавлять/убирать наши адреса - ЗДЕСЬ, а не в .inc.

Вручную добавленные в .inc адреса (кроме CF и OUR_IPS) синхронизатор НЕ
удаляет - сохраняет и пишет об этом в лог. Так правка руками не пропадёт молча.

OPEN_PATHS - пути, открытые ВСЕМ (мимо гейта): служебные точки сервиса проверки
прокси. Запрос к ним приходит с чужого адреса (человек идёт через прокси), и гейт
закрывал бы их наглухо.

Уведомление об изменении: письмо на шину AIUI-BUS (stas/cfwatch -> stas/cody)
+ строка в /var/log/cf_ips_sync.log. Молчит, когда ничего не менялось.

Режимы:
  (без аргументов)  синхронизация (так зовёт systemd-таймер cf-ips-sync.timer)
  --check           только проверить: что отдаёт API, что лежит в .inc, чем
                    отличается. Ничего не пишет.
  --force           перезаписать .inc, даже если etag не менялся
  --status          показать сохранённое состояние предыдущей синхронизации
  --api-url URL     взять список из другого места (file:///tmp/x.json) - для
                    живого теста отката; в письме помечается как тест
  --no-notify       не отправлять письмо на шину (ручной прогон, отладка)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

API_URL = "https://api.cloudflare.com/client/v4/ips"
INC = "/etc/apache2/cody-origin-only.inc"
STATE = "/root/CleanProd/data/cf_ips_state.json"
BACKUP_DIR = "/root/CleanProd/data/backups/cf_ips"
LOG = "/var/log/cf_ips_sync.log"
LOG_MAX = 1000000                       # 1 МБ, потом .log.1
NODES_JSON = "/opt/agentbus/nodes.json"
BUS_SEND = "http://127.0.0.1:8090/v1/bus/send"
BUS_NODE = "stas"                       # узел, которым подписываемся (наш)
BUS_FROM = "stas/cfwatch"               # отдельный агент-отправитель: автоответ
BUS_TO = "stas/cody"                    # мне в ящик
UA = "cf-ips-sync/1.0 (Cody, AI_UI)"
PROBE_HOST = "teaindigo.com"            # домен под тем же гейтом, для живой пробы
# Пути, открытые ВСЕМ - даже если запрос пришёл НЕ через Cloudflare. Это служебные
# точки сервиса проверки прокси (/ip, /geo, /ping): их смысл - ответить тому, кто
# пришёл ЧЕРЕЗ прокси, а такой запрос всегда идёт с ЧУЖОГО адреса, и гейт его рубит.
# Живой случай 19.09.2026: без этого все живые прокси выглядели мёртвыми (403),
# ручное назначение прокси снималось, ящики уезжали напрямую с домашнего IP.
# Пусто = открытых путей нет.
OPEN_PATHS = "ip|geo|ping"
TIMEOUT = 25
NO_NOTIFY = False                       # поднимает ключ --no-notify (ручной прогон)

# НАШИ адреса. Не диапазоны Cloudflare - синхронизатор их не переписывает.
OUR_IPS = [
    "127.0.0.1",
    "::1",
    "example-vps",        # сам сервер
    "example-ip",       # дом, статический
    "example-ip",       # мобильный Киевстар
    "x.x.x.x/24",     # пул L2TP-туннеля
]

# Якоря: если их нет в ответе - считаем ответ битым и ничего не трогаем.
ANCHOR_V4 = ("104.16.0.0/13", "172.64.0.0/13")
ANCHOR_V6 = ("2400:cb00::/32", "2606:4700::/32")

RE_V4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})$")
RE_V6 = re.compile(r"^[0-9a-fA-F:]+/\d{1,3}$")
RE_MATCH = re.compile(r"-ipmatch\s+'([^']+)'")

HEADER = """# cody-origin-only.inc - пускать на origin только соединения от Cloudflare.
# ФАЙЛ СГЕНЕРИРОВАН: tools/cf_ips_sync.py (systemd-таймер cf-ips-sync.timer).
# Руками здесь править нечего: наши адреса живут в OUR_IPS внутри скрипта,
# вручную добавленные строки синхронизатор сохраняет.
# Список CF: %(api)s  etag=%(etag)s
# Обновлён: %(when)s   (v4: %(n4)d, v6: %(n6)d)
# ОТКАТ:  sudo bash -c ': > %(inc)s' && sudo systemctl reload apache2
"""


# --------------------------------------------------------------------- журнал
def log(msg, quiet=False):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if not quiet:
        print(line, flush=True)
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            shutil.move(LOG, LOG + ".1")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception as exc:                              # noqa: BLE001
        print("log write failed: %s" % exc, file=sys.stderr)


# ---------------------------------------------------------------- сеть и файлы
def _http_get(url):
    """(текст, etag). requests, если он есть; иначе urllib. file:// - тоже ок."""
    headers = {}
    if url.startswith("file://"):
        with open(url[7:], "rb") as fh:
            return fh.read().decode("utf-8"), ""
    try:
        import requests
        resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
        if resp.status_code != 200:
            raise RuntimeError("http %s" % resp.status_code)
        return resp.text, (resp.headers.get("etag") or "")
    except ImportError:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:   # noqa: S310
            return resp.read().decode("utf-8"), (resp.headers.get("ETag") or "")


def _ok_v4(item):
    m = RE_V4.match(item)
    if not m:
        return False
    octets = [int(m.group(i)) for i in (1, 2, 3, 4)]
    prefix = int(m.group(5))
    return all(0 <= o <= 255 for o in octets) and 8 <= prefix <= 32


def _ok_v6(item):
    m = RE_V6.match(item)
    if not m:
        return False
    return 16 <= int(item.rsplit("/", 1)[1]) <= 128


def parse_api(text):
    """Строгий разбор ответа. Возвращает (v4, v6) или бросает RuntimeError."""
    try:
        data = json.loads(text)
    except Exception as exc:                              # noqa: BLE001
        raise RuntimeError("ответ не JSON: %s" % exc)
    if not isinstance(data, dict) or data.get("success") is not True:
        raise RuntimeError("success != true")
    res = data.get("result") or {}
    v4 = [str(x) for x in (res.get("ipv4_cidrs") or [])]
    v6 = [str(x) for x in (res.get("ipv6_cidrs") or [])]
    if len(v4) < 5 or len(v6) < 3:
        raise RuntimeError("слишком короткие списки: v4=%d v6=%d" % (len(v4), len(v6)))
    bad = [x for x in v4 if not _ok_v4(x)] + [x for x in v6 if not _ok_v6(x)]
    if bad:
        raise RuntimeError("некорректные CIDR: %s" % ", ".join(bad[:3]))
    for anchor in ANCHOR_V4:
        if anchor not in v4:
            raise RuntimeError("нет якоря %s" % anchor)
    for anchor in ANCHOR_V6:
        if anchor not in v6:
            raise RuntimeError("нет якоря %s" % anchor)
    return sorted(v4), sorted(v6)


def read_inc():
    try:
        with open(INC, encoding="utf-8") as fh:
            return fh.read()
    except IOError:
        return ""


def read_state():
    try:
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:                                     # noqa: BLE001
        return {}


def write_state(state):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(STATE + ".tmp", STATE)
    except Exception as exc:                              # noqa: BLE001
        log("не смог записать состояние: %s" % exc)


def hand_added(text, known):
    """Адреса, вписанные в .inc руками: не CF и не наши. Их не выбрасываем."""
    keep = []
    for item in RE_MATCH.findall(text or ""):
        if item in known or item in OUR_IPS or item in keep:
            continue
        keep.append(item)
    return keep


def build_inc(v4, v6, etag, api, extra):
    parts = ["%%{CONN_REMOTE_ADDR} -ipmatch '%s'" % x for x in (v4 + v6 + OUR_IPS + extra)]
    expr = " || ".join(parts)
    guard = "!(%s)" % expr
    if OPEN_PATHS:
        # Открытые пути - это про URL, а не про адрес: исключаем их из запрета.
        # Секция <If> работает ТОЛЬКО на уровне vhost: внутри <LocationMatch> она
        # молча не применяется, и origin остаётся открытым всему интернету
        # (проверено живьём 19.09.2026).
        guard += " && !(%%{REQUEST_URI} =~ m#^/(%s)$#)" % OPEN_PATHS
    body = '<If "%s">\n    Require all denied\n</If>\n' % guard
    head = HEADER % {"api": api, "etag": etag or "-", "when": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "n4": len(v4), "n6": len(v6), "inc": INC}
    if OPEN_PATHS:
        head += "# Открытые ВСЕМ пути (мимо гейта, служебные): /%s\n" % OPEN_PATHS.replace("|", ", /")
    if extra:
        head += "# Вручную добавленные строки (сохранены): %s\n" % ", ".join(extra)
    return head + body


def backup_inc():
    text = read_inc()
    if not text:
        return ""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, "cody-origin-only.%s.inc" % time.strftime("%Y%m%d_%H%M%S"))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    keep = sorted(os.listdir(BACKUP_DIR))[:-20]
    for old in keep:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return path


def write_inc(text):
    with open(INC + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(INC + ".tmp", INC)


def run(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=60)
        out = (p.stdout or b"") + (p.stderr or b"")
        return p.returncode, out.decode("utf-8", "replace").strip()
    except Exception as exc:                              # noqa: BLE001
        return 99, str(exc)


def curl_code(args, timeout=15):
    """(код выхода curl, HTTP-код из -w). ВАЖНО: не путать одно с другим."""
    rc, out = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                   "--max-time", str(timeout)] + list(args))
    last = (out or "").strip().splitlines()[-1] if (out or "").strip() else ""
    digits = "".join(ch for ch in last if ch.isdigit())
    return rc, digits


def live_probe():
    """Живая проба: с нашего 127.0.0.1 гейт обязан пропустить."""
    rc, code = curl_code(["-k", "-H", "Host: " + PROBE_HOST, "https://127.0.0.1/"], 12)
    if rc != 0:
        return ""
    return code


# ------------------------------------------------------------------ уведомление
def notify(subject, text):
    """Письмо на шину AIUI-BUS. Лучшее, что можем: не вышло - пишем в лог."""
    if NO_NOTIFY:
        log("уведомление не отправлялось (--no-notify): %s" % subject)
        return False
    try:
        with open(NODES_JSON, encoding="utf-8") as fh:
            keys = json.load(fh)
        key = str(keys.get(BUS_NODE) or "")
        if not key:
            log("уведомление не отправлено: в %s нет узла %s" % (NODES_JSON, BUS_NODE))
            return False
        payload = json.dumps({"from": BUS_FROM, "to": BUS_TO, "kind": "note",
                              "id": "cfips-%d" % int(time.time()),
                              "body": "%s\n%s" % (subject, text)}, ensure_ascii=False)
        rc, code = curl_code(["-X", "POST", BUS_SEND,
                              "-H", "Content-Type: application/json",
                              "-H", "X-Bus-Node: " + BUS_NODE,
                              "-H", "X-Bus-Key: " + key,
                              "--data-binary", payload], 15)
        if rc != 0 or code != "200":
            log("уведомление не ушло: rc=%s http=%s" % (rc, code or "-"))
            return False
        log("уведомление отправлено: %s -> %s" % (BUS_FROM, BUS_TO))
        return True
    except Exception as exc:                              # noqa: BLE001
        log("уведомление не ушло: %s" % exc)
        return False


# ------------------------------------------------------------------- основное
def diff(a, b):
    return ([x for x in b if x not in a], [x for x in a if x not in b])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="только показать, не писать")
    ap.add_argument("--force", action="store_true", help="перезаписать даже без изменений")
    ap.add_argument("--status", action="store_true", help="состояние прошлой синхронизации")
    ap.add_argument("--api-url", default=API_URL, help="источник списка (для теста)")
    ap.add_argument("--no-notify", action="store_true", help="не писать письмо на шину")
    args = ap.parse_args()

    global NO_NOTIFY
    NO_NOTIFY = bool(args.no_notify)

    if args.status:
        st = read_state()
        if not st:
            print("состояния нет - синхронизация ещё не проходила")
            return 0
        print("Последняя проверка: %s" % st.get("checked"))
        print("Последнее изменение: %s" % (st.get("changed") or "не менялось"))
        print("etag: %s" % st.get("etag"))
        print("Диапазонов: v4=%s v6=%s" % (st.get("n4"), st.get("n6")))
        print("Результат: %s" % st.get("result"))
        return 0

    state = read_state()
    api = args.api_url
    is_test = api != API_URL
    test_note = "\n(ТЕСТ: список брался не из API, а из %s)" % api if is_test else ""
    try:
        text, etag = _http_get(api)
        v4, v6 = parse_api(text)
    except Exception as exc:                              # noqa: BLE001
        msg = "список Cloudflare НЕ обновлён: %s" % exc
        log("ОШИБКА: " + msg)
        notify("⚠ CF-сторож: список не обновлён", msg +
               "\nГейт продолжает работать на прошлом списке (/etc/apache2/cody-origin-only.inc)."
               "\nЛог: %s" % LOG + test_note)
        return 2

    st4 = [str(x) for x in (state.get("v4") or [])]
    st6 = [str(x) for x in (state.get("v6") or [])]
    st_etag = str(state.get("etag") or "")
    known = set(st4) | set(st6) | set(OUR_IPS) | set(v4) | set(v6)
    extra = hand_added(read_inc(), known)
    same = (etag and etag == st_etag) or (not etag and v4 == st4 and v6 == st6)

    if args.check:
        add4, del4 = diff(st4, v4)
        add6, del6 = diff(st6, v6)
        print("API: %s" % api)
        print("etag: %s (в состоянии: %s)" % (etag or "-", st_etag or "-"))
        print("v4=%d v6=%d" % (len(v4), len(v6)))
        print("Было в состоянии: v4=%d v6=%d" % (len(st4), len(st6)))
        print("Новых v4: %s" % (add4 or "нет"))
        print("Ушедших v4: %s" % (del4 or "нет"))
        print("Новых v6: %s" % (add6 or "нет"))
        print("Ушедших v6: %s" % (del6 or "нет"))
        print("Вручную в .inc: %s" % (extra or "нет"))
        print("Вывод: %s" % ("менять нечего" if (same and not args.force) else "нужно обновить"))
        return 0

    if same and not args.force:
        log("изменений нет (etag=%s, v4=%d, v6=%d)" % (etag or "-", len(v4), len(v6)))
        state.update({"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "result": "без изменений"})
        write_state(state)
        return 0

    add4, del4 = diff(st4, v4)
    add6, del6 = diff(st6, v6)
    if state and not st4:
        summary = "первое заполнение состояния: v4=%d v6=%d" % (len(v4), len(v6))
    else:
        summary = ("+v4 %s / -v4 %s / +v6 %s / -v6 %s"
                   % (add4 or "нет", del4 or "нет", add6 or "нет", del6 or "нет"))

    backup = backup_inc()
    old_text = read_inc()
    write_inc(build_inc(v4, v6, etag, api, extra))
    log("файл обновлён (бэкап: %s): %s" % (backup or "не было", summary))

    code, out = run(["apachectl", "configtest"])
    if code != 0:
        write_inc(old_text)
        log("ОТКАТ: configtest не прошёл: %s" % out)
        notify("⚠ CF-сторож: ОТКАТ, список не применён",
               "configtest не прошёл: %s\nФайл вернул прежний список, Apache не трогал. Лог: %s"
               % (out, LOG) + test_note)
        return 3

    code, out = run(["systemctl", "reload", "apache2"])
    if code != 0:
        write_inc(old_text)
        run(["systemctl", "reload", "apache2"])
        log("ОТКАТ: reload не прошёл: %s" % out)
        notify("⚠ CF-сторож: ОТКАТ, Apache не перезагрузился",
               "reload: %s\nФайл вернул прежний список. Лог: %s" % (out, LOG) + test_note)
        return 4

    probe = live_probe()
    if not probe or probe[0] not in ("2", "3"):
        write_inc(old_text)
        run(["systemctl", "reload", "apache2"])
        log("ОТКАТ: живая проба дала %s (гейт закрыл наших)" % probe)
        notify("⚠ CF-сторож: ОТКАТ, гейт закрыл наших",
               "Живая проба с 127.0.0.1 вернула %s вместо нормального ответа."
               "\nФайл вернул прежний список, Apache перезагружен. Лог: %s" % (probe, LOG) + test_note)
        return 5

    state = {"etag": etag, "v4": v4, "v6": v6, "n4": len(v4), "n6": len(v6),
             "checked": time.strftime("%Y-%m-%d %H:%M:%S"),
             "changed": time.strftime("%Y-%m-%d %H:%M:%S"),
             "result": "обновлён, configtest ok, reload ok, проба %s" % probe,
             "api": api}
    write_state(state)
    log("применено: v4=%d v6=%d, проба %s, наши адреса в файле: %d"
        % (len(v4), len(v6), probe, len(OUR_IPS)))

    note = test_note
    notify("CF-сторож: список Cloudflare обновлён",
           "%s\nДиапазонов в файле: v4=%d, v6=%d. configtest ok, Apache перезагружен, "
           "проба с 127.0.0.1 - %s.%s\nБэкап прежнего: %s\nЛог: %s"
           % (summary, len(v4), len(v6), probe, note, backup or "-", LOG))
    return 0


if __name__ == "__main__":
    sys.exit(main())
