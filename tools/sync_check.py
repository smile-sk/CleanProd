#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка «локальные копии <-> боевой сервер» для CleanProd.

Зачем: 26.10.2026 выяснилось, что код на сервере был новее локальных копий
(hardening), а лендинг наоборот - локальные были новее репо-копий на сервере.
Половина расхождений была не в содержимом, а в концах строк (CRLF/LF), из-за
чего сравнение md5 «в лоб» врало. Поэтому здесь md5 считается по содержимому
с вырезанными \\r.

Что даёт: таблицу «файл: ok / РАСХОЖДЕНИЕ / нет локально / нет на сервере»
и код возврата 1, если есть расхождения (годится для крона и для проверки
перед правкой).

Доступ: переменные окружения VPS_HOST / VPS_USER / VPS_PASS, иначе файл
tools/vps_access.json рядом со скриптом: {"host": "...", "user": "...", "password": "..."}

Запуск:  python tools/sync_check.py            # только отчёт
         python tools/sync_check.py --quiet    # только итог и код возврата
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

try:
    import paramiko
except ImportError:  # pragma: no cover
    print("нужен paramiko: pip install paramiko")
    raise SystemExit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # C:\Documents\Projects\CleanProd

# ── что с чем сверять ────────────────────────────────────────────────────────
# (имя группы, каталог на сервере, каталог локально, маска файлов, нужен sudo)
GROUPS = [
    ("код API",        "/root/CleanProd/payment_api",         os.path.join(ROOT, "payment_api"),          ("*.py", "*.md"), True),
    ("репо-лендинг",   "/root/CleanProd/payment_api/landing", os.path.join(ROOT, "payment_api", "landing"), ("*.html", "*.txt"), True),
    ("лендинг pay",    "/var/www/pay.cleantrade.info/public_html", os.path.join(ROOT, "payment_api", "landing"), ("*.html", "*.txt", "*.png"), True),
    ("лендинг signals", "/var/www/cleantrade.info/public_html", os.path.join(ROOT, "cleantrade.info_landing"), ("*.html", "*.txt", "*.png"), True),
    ("боты",           "/root/CleanProd/bots",                os.path.join(ROOT, "bots"),                 ("*.py",), True),
    ("корень",         "/root/CleanProd",                     ROOT,                                       ("config.py", "main.py", "README.md", ".env.example"), True),
    ("инструменты",    "/root/CleanProd/tools",               os.path.join(ROOT, "tools"),                ("*.py", "*.sh"), True),
    ("доки",           "/root/CleanProd/docs",                os.path.join(ROOT, "docs"),                 ("*.md",), True),
]

SKIP_SUBSTR = (".bak", "__pycache__", ".pyc", ".corrupt")
# живут только локально: сверять их с сервером нечего
LOCAL_ONLY = {"sync_check.py", "vps_map.py", "vps_access.json"}


def connect():
    host = os.environ.get("VPS_HOST")
    user = os.environ.get("VPS_USER")
    pwd = os.environ.get("VPS_PASS")
    if not (host and user and pwd):
        cfg = os.path.join(HERE, "vps_access.json")
        if os.path.exists(cfg):
            d = json.load(open(cfg, encoding="utf-8"))
            host, user, pwd = d.get("host"), d.get("user"), d.get("password")
    if not (host and user and pwd):
        raise SystemExit("нет доступа: задай VPS_HOST/VPS_USER/VPS_PASS или tools/vps_access.json")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=pwd, timeout=25)
    return c, pwd


def remote_md5(client, pwd, path, patterns):
    """{имя: (md5 по содержимому без \\r, размер)} для файлов каталога.

    Скрипт уезжает на сервер в base64: если подставлять его в командную строку,
    внешняя оболочка раскрывает $(...) ДО sudo, и md5 считается от пустой строки.
    """
    mask = " ".join("-name '%s' -o" % p for p in patterns).rstrip(" -o")
    script = (
        "cd %s 2>/dev/null || exit 0\n"
        "find . -maxdepth 1 -type f \\( %s \\) -print0 |\n"
        "while IFS= read -r -d '' f; do\n"
        "  n=$(basename \"$f\")\n"
        "  h=$(tr -d '\\r' < \"$f\" | md5sum | cut -d' ' -f1)\n"
        "  z=$(stat -c%%s \"$f\")\n"
        "  printf '%%s|%%s|%%s\\n' \"$n\" \"$h\" \"$z\"\n"
        "done\n" % (path, mask)
    )
    b64 = base64.b64encode(script.encode()).decode()
    inner = "echo %s | base64 -d > /tmp/_sync_md5.sh && bash /tmp/_sync_md5.sh" % b64
    cmd = "echo %s | sudo -S bash -c %s" % (pwd, json.dumps(inner))
    _in, out, _err = client.exec_command(cmd, timeout=120)
    res = {}
    for line in out.read().decode("utf-8", "replace").splitlines():
        parts = line.split("|")
        if len(parts) == 3 and len(parts[1]) == 32 and parts[2].isdigit():
            res[parts[0]] = (parts[1], int(parts[2]))
    return res


def local_md5(path):
    raw = open(path, "rb").read()
    return hashlib.md5(raw.replace(b"\r\n", b"\n").replace(b"\r", b"")).hexdigest(), len(raw)


def main() -> int:
    quiet = "--quiet" in sys.argv
    client, pwd = connect()
    drift = 0
    try:
        for name, rpath, lpath, patterns, sudo in GROUPS:
            srv = remote_md5(client, pwd, rpath, patterns) if sudo else {}
            loc = {}
            if os.path.isdir(lpath):
                for n in os.listdir(lpath):
                    p = os.path.join(lpath, n)
                    if not os.path.isfile(p) or n in LOCAL_ONLY or any(s in n for s in SKIP_SUBSTR):
                        continue
                    if any(n.endswith(pat.lstrip("*")) for pat in patterns):
                        loc[n] = local_md5(p)
            rows = []
            for n in sorted(set(srv) | set(loc)):
                s, l = srv.get(n), loc.get(n)
                if s and l:
                    rows.append((n, "ok" if s[0] == l[0] else "РАСХОЖДЕНИЕ", l[1], s[1]))
                elif s:
                    rows.append((n, "нет локально", 0, s[1]))
                else:
                    rows.append((n, "нет на сервере", l[1], 0))
            bad = [r for r in rows if r[1] != "ok"]
            drift += len(bad)
            if not quiet:
                print("\n== %s (%s)" % (name, rpath))
                for n, st, ls, ss in rows:
                    if st == "ok":
                        if not quiet:
                            print("   ok  %-24s %d б" % (n, ss))
                    else:
                        print("   !!  %-24s %-16s локально %d б / сервер %d б" % (n, st, ls, ss))
            elif bad:
                for n, st, ls, ss in bad:
                    print("!! %s: %-24s %s (локально %d / сервер %d)" % (name, n, st, ls, ss))
    finally:
        client.close()
    print("\nИТОГ: расхождений %d" % drift)
    return 0 if drift == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
