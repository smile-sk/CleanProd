#!/usr/bin/env python3
"""Постоянный блок AS-сетей на периметре CleanTrade (pay.cleantrade.info).

Зачем: 05.10.2026 кампания шла из двух хостеров - Cloudzy/RouterHosting (AS14956)
и Lucidacloud (AS139659). Автобан работает per-IP с окном 600 с, поэтому "вежливые"
заходы (по 30-200 запросов на адрес, растянутые на часы) в бан не попадали.
Оператор сказал: эти две конторы заблокировать по умолчанию и навсегда.

Что делает: читает data/perimeter/blocklist_static.txt, собирает цепочки
CTSTATIC (IPv4) и CTSTATIC6 (IPv6) и ставит прыжок из INPUT первым правилом.
Идемпотентно: повторный запуск цепочки пересобирает, дублей не плодит.

Предохранители (строка SKIP, не применяется):
  * префикс накрывает собственный адрес сервера, SSH-клиента или хост из
    whitelist-правил CODY_WL (иначе можно отрезать себе доступ);
  * префикс пересекается с диапазонами Cloudflare (сайт за CF-прокси).

Запуск: python3 tools/static_block.py [--dry-run]
"""
import ipaddress
import json
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_FILE = os.path.join(BASE, "data", "perimeter", "blocklist_static.txt")
CF_FILE = os.path.join(BASE, "data", "cf_ips_state.json")
RULES4 = "/etc/iptables/rules.v4"

CF_FALLBACK = """173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22
141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22
198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13
131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32
2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32""".split()


def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def read_prefixes():
    out = []
    with open(LIST_FILE, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                out.append(ipaddress.ip_network(line, strict=False))
            except ValueError:
                print("BAD LINE: %r" % line)
    return out


def cloudflare():
    nets = []
    try:
        data = json.load(open(CF_FILE, encoding="utf-8"))
        items = data.get("ips") or data.get("nets") or data.get("prefixes") or []
        for x in items:
            if isinstance(x, str):
                nets.append(ipaddress.ip_network(x, strict=False))
            elif isinstance(x, dict):
                for k in ("ip", "prefix", "net", "cidr"):
                    if x.get(k):
                        nets.append(ipaddress.ip_network(str(x[k]), strict=False))
                        break
    except Exception:
        pass
    if not nets:
        nets = [ipaddress.ip_network(x) for x in CF_FALLBACK]
    return nets


def protected_hosts():
    """Адреса, которые нельзя потерять: сам сервер, SSH-клиент, whitelist CODY_WL."""
    hosts = []
    rc, txt = sh("ip -o -4 addr show scope global; ip -o -6 addr show scope global")
    for m in re.finditer(r"inet6?\s+([0-9a-fA-F:.]+)/", txt):
        hosts.append(m.group(1))
    conn = os.environ.get("SSH_CONNECTION", "").split()
    if len(conn) >= 3:
        hosts.append(conn[2])
    try:
        rules = open(RULES4, encoding="utf-8").read()
        for m in re.finditer(r"-s\s+([0-9a-fA-F:.]+/\d+).*CODY_WL", rules):
            hosts.append(m.group(1).split("/")[0])
    except Exception:
        pass
    nets = []
    for h in set(hosts):
        try:
            nets.append(ipaddress.ip_network(h, strict=False))
        except ValueError:
            pass
    return nets


def chains(tool, chain, nets, journal, dry):
    code, _ = sh("%s -n -L %s 2>/dev/null" % (tool, chain))
    if code != 0 and not dry:
        sh("%s -N %s" % (tool, chain))
    if dry:
        print("  %s: цепочка %s, добавили бы %d DROP" % (tool, chain, len(nets)))
        return
    sh("%s -F %s" % (tool, chain))
    for n in nets:
        sh("%s -A %s -s %s -j DROP" % (tool, chain, n))
    while sh("%s -C INPUT -j %s" % (tool, chain))[0] == 0:
        sh("%s -D INPUT -j %s" % (tool, chain))
    code, out = sh("%s -I INPUT 1 -j %s" % (tool, chain))
    journal.append("%s: %s <- %d префиксов, прыжок из INPUT #1" % (tool, chain, len(nets)))


def main():
    dry = "--dry-run" in sys.argv
    nets = read_prefixes()
    cf = cloudflare()
    guards = protected_hosts()
    v4, v6, skipped = [], [], []
    for n in nets:
        bad = None
        for g in cf:
            if g.version == n.version and n.overlaps(g):
                bad = "пересечение с Cloudflare %s" % g
        for g in guards:
            if g.version == n.version and n.overlaps(g):
                bad = "накрывает наш адрес %s" % g
        if bad:
            skipped.append("%s - SKIP (%s)" % (n, bad))
        elif n.version == 4:
            v4.append(str(n))
        else:
            v6.append(str(n))
    v4, v6 = sorted(set(v4)), sorted(set(v6))
    print("Файл: %s" % LIST_FILE)
    print("К блокировке: IPv4=%d IPv6=%d, пропущено=%d" % (len(v4), len(v6), len(skipped)))
    for s in skipped:
        print("  " + s)
    journal = []
    chains("iptables", "CTSTATIC", v4, journal, dry)
    chains("ip6tables", "CTSTATIC6", v6, journal, dry)
    for j in journal:
        print("  " + j)
    if dry:
        print("DRY-RUN: ничего не меняли.")
        return
    code, out = sh("iptables -S INPUT | head -3")
    print("INPUT сейчас:\n" + out.strip())


if __name__ == "__main__":
    main()
