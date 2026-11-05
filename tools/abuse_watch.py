#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Авто-блокировка абуза для CleanProd/VPS.

Слушает логи Apache и ленту событий API, считает поведение по каждому IP
за окно и банит:
  * ядро      -> iptables/ip6tables (DROP в INPUT, мгновенно, без reload);
  * Apache    -> rules.conf, включается ВНУТРЬ каждого vhost по
                 REMOTE_ADDR и CF-Connecting-IP (правила из главного
                 конфига до vhost НЕ достают - проверено 18.09 живьём);
  * оба списка      -> /root/CleanProd/data/blocklist.json (его читает API).

Запуск (обычно systemd-юнитом):
  abuse_watch.py                 демон
  abuse_watch.py --once          один проход
  abuse_watch.py --dry-run       показать, кого бы забанил (ничего не пишет)
  abuse_watch.py --status        что сейчас в бан-листах
  abuse_watch.py --ban IP [часы] [причина]
  abuse_watch.py --unban IP

Не банит никогда: localhost, адреса Cloudflare (иначе отрежет весь трафик
через прокси) и адреса из whitelist в autoban_config.json.
"""

import argparse
import datetime as dt
import glob
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import deque

BASE = "/root/CleanProd"
DATA = os.path.join(BASE, "data")
CFG_PATH = os.path.join(DATA, "autoban_config.json")
STATE_PATH = os.path.join(DATA, "autoban_state.json")
BANS_PATH = os.path.join(DATA, "autoban_bans.json")
APP_BLOCKLIST = os.path.join(DATA, "blocklist.json")
AB_DIR = "/etc/apache2/autoban"
RULES_PATH = os.path.join(AB_DIR, "rules.conf")
VHOST_BACKUP = os.path.join(BASE, "backups", "apache_vhost_rules_20260918")
LOG_PATH = "/var/log/cleantrade_autoban.log"
CF_CACHE = os.path.join(DATA, "cf_ranges.json")

# Официальные диапазоны Cloudflare (используются, если сети нет и кэша нет).
CF_STATIC = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
]

HOSTILE = re.compile(
    r"(?:phpmyadmin|pma|wp-login|wp-admin|xmlrpc\.php|adminer|\.env|\.git|"
    r"\.htpasswd|\.htaccess|config\.php|config\.js|backup\.(?:sql|zip|tar|gz)|"
    r"phpinfo|server-status|/dump|/\.idea|/vendor/|/\.svn|setup\.php|"
    r"/(?:db|mysql|sql)\.(?:sql|zip)|/admin\.(?:bak|old)|/web\.env"
    # --- добавлено 17.10.2026: то, чем сканеры ходили и НЕ попадались ---
    # Vite/Astro dev-server: /@fs/ читает любой файл на диске
    r"|/@fs/|/userfiles|\.vite/|/_astro/|\.astro\."
    # конфиги и секреты, которые просят сканеры
    r"|\.amplifyrc|\.claude/|\.docker/|\.aws/|\.kube/config|\.npmrc|\.cargo/"
    r"|\.envrc|\.my\.cnf|\.pgpass|\.dbeaver|\.vault-token|\.gitconfig"
    r"|/id_(?:rsa|dsa|ecdsa|ed25519)|\.ssh/|privatekey\.key"
    r"|/rclone\.conf|/values\.yaml|secrets\.(?:json|ya?ml|env)|/secrets/|serviceaccount/token"
    r"|/\.bashrc|/\.zshrc|/proc/self/(?:environ|cmdline|cgroup)"
    # RCE-пробы известных продуктов
    r"|langflow|flowise|inngest|read-document|php-cgi\.exe|/cgi-bin/php"
    r"|allow_url_include|/api/fs/(?:read|exec)|fileView\?file=)",
    re.I,
)
# коды, которые в норме означают "не пустили"
AUTH_FAIL = {401, 403, 429}

# попытки входа в админку OpenCart считаем отдельно: неудачный логин
# отдаёт 200, поэтому на коды тут опираться нельзя
LOGIN_RE = re.compile(r"route=common/login", re.I)
# коды событий API, которые считаем абузом
APP_BAD_KINDS = {
    "auth_fail", "support_auth_fail", "rate_limited", "free_requires_tg",
    "webhook_rejected", "admin_fail", "tg_confirm_failed",
}

APACHE_LINE = re.compile(
    r'^\S+\s+(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<ts>[^\]]+)\]\s+'
    r'"(?P<method>\S+)\s+(?P<path>[^"]*)"\s+(?P<status>\d{3})'
)


def now():
    return time.time()


def iso(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def log(msg, cfg=None):
    line = "[%s] %s" % (iso(now()), msg)
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- конфиг/состояние

def load_cfg():
    base = {
        "interval": 20,
        "window_sec": 600,
        "whitelist": ["127.0.0.1", "::1", "example-vps"],
        "log_globs": ["/var/log/apache2/*access.log", "/var/log/apache2/other_vhosts_access.log"],
        "events_files": [os.path.join(DATA, "events.jsonl")],
        "rules": {"hostile_paths": 5, "auth_fail": 40, "flood": 800, "app_bad": 25,
                  "login_fail": 20, "bad_status": 20, "not_found": 120},
        "ban_seconds": {"hostile_paths": 86400, "auth_fail": 86400,
                        "flood": 3600, "app_bad": 86400, "login_fail": 86400,
                        "bad_status": 86400, "not_found": 21600,
                        "manual": 86400},
        "escalate": 4,
        "max_ban_sec": 604800,
        "subnet_ban_v6": True,
        "enabled": True,
    }
    if os.path.exists(CFG_PATH):
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(base.get(k), dict):
                    base[k].update(v)
                else:
                    base[k] = v
        except Exception as e:
            log("config read error: %s" % e)
    else:
        write_json(CFG_PATH, base)
    return base


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def load_state():
    st = read_json(STATE_PATH, {"bans": {}, "offset": {}})
    st.setdefault("bans", {})
    st.setdefault("offset", {})
    return st


# ---------------------------------------------------------------- cloudflare / whitelist

def cf_networks():
    nets = []
    data = read_json(CF_CACHE, None)
    fresh = data and (now() - data.get("ts", 0) < 86400 * 7)
    if not fresh:
        try:
            import urllib.request
            got = []
            for url in ("https://www.cloudflare.com/ips-v4", "https://www.cloudflare.com/ips-v6"):
                with urllib.request.urlopen(url, timeout=10) as r:
                    got += [l.strip() for l in r.read().decode().splitlines() if l.strip()]
            if got:
                data = {"ts": now(), "nets": got}
                write_json(CF_CACHE, data)
                fresh = True
        except Exception:
            pass
    raw = (data or {}).get("nets") or CF_STATIC
    for r in raw:
        try:
            nets.append(ipaddress.ip_network(r, strict=False))
        except ValueError:
            pass
    return nets


def is_cf(ip, nets):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in nets)


def whitelist_nets(cfg):
    out = []
    for r in cfg.get("whitelist", []):
        try:
            out.append(ipaddress.ip_network(r, strict=False) if "/" in r
                       else ipaddress.ip_network(r + ("/128" if ":" in r else "/32")))
        except ValueError:
            pass
    return out


def is_white(ip, wl):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return any(a in n for n in wl)


# ---------------------------------------------------------------- чтение источников

def read_tail(path, cfg, st):
    """Вернуть новые строки файла, начиная с сохранённого смещения."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    key = "off:" + path
    prev = st["offset"].get(key)
    if prev is None or prev > size:
        prev = max(0, size - int(cfg.get("tail_bytes", 3_000_000)))
    with open(path, "rb") as f:
        f.seek(prev)
        chunk = f.read()
        st["offset"][key] = f.tell()
    text = chunk.decode("utf-8", "replace")
    if prev:
        # первая строка может быть обрезана - выбросить
        nl = text.find("\n")
        text = text[nl + 1:] if nl >= 0 else ""
    return text.splitlines()


def parse_apache_ts(s):
    try:
        return dt.datetime.strptime(s, "%d/%b/%Y:%H:%M:%S %z").timestamp()
    except ValueError:
        return None


def feed_apache(lines, win, cfg, cf, wl, counters, stats):
    processed = 0
    for ln in lines:
        m = APACHE_LINE.match(ln)
        if not m:
            continue
        ip = m.group("ip")
        ts = parse_apache_ts(m.group("ts"))
        if ts is None or ts < now() - win:
            continue
        if is_cf(ip, cf) or is_white(ip, wl):
            continue          # клиент за Cloudflare / наш - не считаем
        status = int(m.group("status"))
        path = m.group("path")
        hostile = bool(HOSTILE.search(path))
        c = counters.setdefault(ip, deque())
        c.append((ts, status, hostile, bool(LOGIN_RE.search(path))))
        processed += 1
    return processed


def feed_events(lines, win, cfg, cf, wl, counters):
    processed = 0
    for ln in lines:
        ln = ln.strip()
        if not ln or not ln.startswith("{"):
            continue
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        d = rec.get("data") if isinstance(rec.get("data"), dict) else {}
        ip = (d.get("ip") or d.get("client_ip") or rec.get("ip")
              or d.get("cf_connecting_ip") or "")
        kind = rec.get("kind") or rec.get("event") or ""
        if not ip or kind not in APP_BAD_KINDS:
            continue
        if is_cf(ip, cf) or is_white(ip, wl):
            continue
        ts = rec.get("ts") or rec.get("time") or rec.get("timestamp")
        try:
            t = float(ts) if isinstance(ts, (int, float)) else \
                dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except Exception:
            t = now()
        if t < now() - win:
            continue
        counters.setdefault(ip, deque()).append((t, "app", False, False))
        if kind in ("auth_fail", "support_auth_fail", "rate_limited", "free_requires_tg"):
            counters[ip].append((t, "app-bad", False, False))
        processed += 1
    return processed


def trim(counters, win):
    edge = now() - win
    for ip in list(counters):
        c = counters[ip]
        while c and c[0][0] < edge:
            c.popleft()
        if not c:
            del counters[ip]


# ---------------------------------------------------------------- решения

def decide(ip, events, cfg):
    """Вернуть (правило, хиты) если IP надо забанить, иначе (None, 0)."""
    rules = cfg["rules"]
    hostile = sum(1 for e in events if e[2])
    login = sum(1 for e in events if len(e) > 3 and e[3])
    authfail = sum(1 for e in events if e[1] in AUTH_FAIL)
    missing = sum(1 for e in events if e[1] == 404)
    total = len(events)
    app_bad = sum(1 for e in events if e[1] == "app-bad")
    app_any = sum(1 for e in events if e[1] == "app")
    if hostile >= rules["hostile_paths"]:
        return "hostile_paths", hostile
    if login >= rules.get("login_fail", 20):
        return "login_fail", login
    # 401/403/429 - «щупают API»: сначала считаем количество на IP, потом бан
    # (17.10.2026, слово Стаса: 429/403 - весомый сигнал, а не мусор)
    if authfail >= rules.get("bad_status", 20):
        return "bad_status", authfail
    # 404-волна: перебор путей на несуществующие адреса - подпись сканера
    if missing >= rules.get("not_found", 120):
        return "not_found", missing
    if authfail >= rules["auth_fail"]:
        return "auth_fail", authfail
    if app_bad >= rules["app_bad"]:
        return "app_bad", app_bad
    if app_any >= rules["app_bad"]:
        return "app_bad", app_any
    if total >= rules["flood"]:
        return "flood", total
    return None, 0


def ban(ip, rule, hits, cfg, st, hours=None, reason=None):
    b = cfg["ban_seconds"]
    dur = int(hours * 3600) if hours else int(b.get(rule, 86400))
    prev = st["bans"].get(ip) or {}
    n = int(prev.get("bans", 0))
    if n:
        dur = min(dur * int(cfg.get("escalate", 4)) ** min(n, 3), int(cfg["max_ban_sec"]))
    st["bans"][ip] = {
        "until": now() + dur,
        "rule": rule,
        "reason": reason or rule,
        "hits": hits,
        "bans": n + 1,
        "first_seen": prev.get("first_seen") or iso(now()),
        "last_seen": iso(now()),
    }
    net = subnet_of(ip) if cfg.get("subnet_ban_v6", True) else None
    if net:
        st["bans"].setdefault(net, {
            "until": now() + dur, "rule": "subnet_of_" + rule, "reason": "подсеть " + ip,
            "hits": hits, "bans": 1, "first_seen": iso(now()), "last_seen": iso(now()),
        })
    return dur


def subnet_of(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if a.version == 6 and not a.is_loopback:
        return str(ipaddress.ip_network(ip + "/64", strict=False))
    return None


def expire(st):
    t = now()
    gone = [ip for ip, v in st["bans"].items() if v.get("until", 0) <= t]
    for ip in gone:
        del st["bans"][ip]
    return gone


def ip_regex(key):
    """Ключ бан-листа -> regex для REMOTE_ADDR / CF-Connecting-IP."""
    if "/" in key:
        try:
            net = ipaddress.ip_network(key, strict=False)
        except ValueError:
            return None
        if net.version == 4:
            full = net.prefixlen // 8
            if full < 2:
                return None
            return r"^" + r"\.".join(str(net.network_address).split(".")[:full]) + r"\."
        full = net.prefixlen // 16
        if full < 3:
            return None
        pref = ":".join(g.lower() for g in str(net.network_address).split(":")[:full])
        return "^" + pref + "(:|$)"
    if ":" in key:
        return "^" + key.lower() + "$"
    return "^" + key.replace(".", r"\.") + "$"


def ensure_includes():
    """IncludeOptional rules.conf внутрь каждого vhost - один раз.

    Почему внутрь: RewriteRule/RewriteCond, объявленные в главном конфиге
    Apache (conf-enabled), до name-based vhost'ов НЕ доходят - проверено
    18.09.2026 живьём: правило в главном конфиге давало 404/200, то же
    правило внутри vhost - 403.
    """
    os.makedirs(VHOST_BACKUP, exist_ok=True)
    line = "    IncludeOptional " + RULES_PATH + "\n"
    changed = 0
    for path in sorted(glob.glob("/etc/apache2/sites-enabled/*.conf")):
        try:
            with open(path, encoding="utf-8") as f:
                s = f.read()
        except OSError:
            continue
        if RULES_PATH in s or "</VirtualHost>" not in s:
            continue
        bak = os.path.join(VHOST_BACKUP, os.path.basename(path))
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s.replace("</VirtualHost>", line + "</VirtualHost>"))
        changed += 1
        log("include правил добавлен в " + os.path.basename(path))
    return changed


def _ensure_chain(tool, name, addrs):
    """Отдельная цепочка в INPUT: прямой клиент режется ядром."""
    if subprocess.run([tool, "-n", "-L", name], capture_output=True).returncode != 0:
        subprocess.run([tool, "-N", name], capture_output=True)
    if subprocess.run([tool, "-C", "INPUT", "-j", name], capture_output=True).returncode != 0:
        subprocess.run([tool, "-I", "INPUT", "1", "-j", name], capture_output=True)
        log("%s: цепочка %s подключена к INPUT" % (tool, name))
    cur = set()
    out = subprocess.run([tool, "-S", name], capture_output=True, text=True).stdout or ""
    for ln in out.splitlines():
        m = re.match(r"-A %s -s (\S+) -j DROP" % name, ln)
        if m:
            cur.add(m.group(1))
    want = set(addrs)
    for a in sorted(want - cur):
        subprocess.run([tool, "-A", name, "-s", a, "-j", "DROP"], capture_output=True)
        log("%s: DROP %s" % (tool, a))
    for a in sorted(cur - want):
        subprocess.run([tool, "-D", name, "-s", a, "-j", "DROP"], capture_output=True)
        log("%s: снят %s" % (tool, a))


def apply_iptables(st):
    _ensure_chain("iptables", "CTBAN", [k for k in st["bans"] if ":" not in k])
    _ensure_chain("ip6tables", "CTBAN6", [k for k in st["bans"] if ":" in k])


def write_map(st):
    """Единый бан-лист: rules.conf (vhost) + iptables. Имя оставлено для
    совместимости с вызовами в ban/unban/цикле."""
    os.makedirs(AB_DIR, exist_ok=True)
    pats = [p for p in (ip_regex(k) for k in sorted(st["bans"])) if p]
    if pats:
        rx = "(?i)(" + "|".join(pats) + ")"
        new = ("# Сгенерировано abuse_watch.py - правки затрёт. Включается внутрь vhost.\n"
               "RewriteEngine On\n"
               "RewriteCond %{REMOTE_ADDR} " + rx + " [OR]\n"
               "RewriteCond %{HTTP:CF-Connecting-IP} " + rx + "\n"
               "RewriteRule ^ - [F,L]\n")
    else:
        new = "# Сгенерировано abuse_watch.py - список пуст\n"
    old = ""
    try:
        with open(RULES_PATH, encoding="utf-8") as f:
            old = f.read()
    except OSError:
        pass
    if new != old:
        with open(RULES_PATH + ".tmp", "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(RULES_PATH + ".tmp", RULES_PATH)
        os.chmod(RULES_PATH, 0o644)
        n = ensure_includes()
        r = subprocess.run(["apache2ctl", "configtest"], capture_output=True, text=True)
        if r.returncode != 0:
            log("configtest ПРОВАЛ, откат: " + (r.stderr or "").strip()[:200])
            for path in glob.glob("/etc/apache2/sites-enabled/*.conf"):
                bak = os.path.join(VHOST_BACKUP, os.path.basename(path))
                if os.path.exists(bak):
                    shutil.copy2(bak, path)
            return
        g = subprocess.run(["apache2ctl", "graceful"], capture_output=True, text=True)
        log("правила обновлены (%d банов, +%d include) -> graceful rc=%s"
            % (len(pats), n, g.returncode))
    apply_iptables(st)




def write_app_blocklist(st):
    cur = read_json(APP_BLOCKLIST, {})
    manual = [x for x in (cur.get("blocked") or [])
              if x not in st["bans"] and subnet_of(x) not in st["bans"]]
    auto = sorted(st["bans"].keys())
    obj = {
        "blocked": manual + auto,
        "added": cur.get("added", dt.date.today().isoformat()),
        "updated": iso(now()),
        "reason": cur.get("reason", "abuse"),
        "auto": len(auto),
        "manual": len(manual),
    }
    write_json(APP_BLOCKLIST, obj)


def write_bans_audit(st):
    obj = {ip: {"until": iso(v["until"]), "rule": v["rule"], "reason": v["reason"],
                "hits": v["hits"], "bans": v["bans"], "first_seen": v["first_seen"]}
           for ip, v in sorted(st["bans"].items())}
    write_json(BANS_PATH, obj)


# ---------------------------------------------------------------- CLI

def cmd_status(cfg, st):
    print("Авто-блокировка: %s | окно %s с, порог простоя %s с"
          % ("включена" if cfg.get("enabled") else "ВЫКЛЮЧЕНА",
             cfg["window_sec"], cfg["interval"]))
    if not st["bans"]:
        print("Активных банов нет.")
        return
    print("%-46s %-9s %-6s %s" % ("IP / подсеть", "до", "хитов", "причина"))
    for ip, v in sorted(st["bans"].items(), key=lambda kv: -kv[1]["until"]):
        left = v["until"] - now()
        print("%-46s %-9s %-6s %s" % (ip, "%dh%02d" % (left // 3600, (left % 3600) // 60),
                                      v.get("hits", ""), v.get("reason", "")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--ban", metavar="IP")
    ap.add_argument("--unban", metavar="IP")
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--reason", default=None)
    a = ap.parse_args()

    cfg = load_cfg()
    st = load_state()

    if a.status:
        cmd_status(cfg, st)
        return
    if a.ban:
        d = ban(a.ban, "manual", 0, cfg, st, hours=a.hours, reason=a.reason or "вручную")
        write_map(st); write_app_blocklist(st); write_bans_audit(st)
        save(st)
        log("BAN %s на %s ч (вручную: %s)" % (a.ban, d // 3600, a.reason or "-"))
        return
    if a.unban:
        for k in (a.unban, subnet_of(a.unban)):
            if k and k in st["bans"]:
                del st["bans"][k]
        write_map(st); write_app_blocklist(st); write_bans_audit(st)
        save(st)
        log("UNBAN %s" % a.unban)
        return

    cf = cf_networks()
    wl = whitelist_nets(cfg)
    log("старт: %d сетей Cloudflare, whitelist %s, окно %s с"
        % (len(cf), cfg["whitelist"], cfg["window_sec"]))

    counters = {}
    while True:
        if not cfg.get("enabled", True):
            time.sleep(int(cfg["interval"]))
            cfg = load_cfg()
            continue
        stats = {}
        for pat in cfg["log_globs"]:
            for path in glob.glob(pat):
                feed_apache(read_tail(path, cfg, st), cfg["window_sec"], cfg, cf, wl,
                            counters, stats)
        for path in cfg["events_files"]:
            if os.path.exists(path):
                feed_events(read_tail(path, cfg, st), cfg["window_sec"], cfg, cf, wl, counters)
        trim(counters, cfg["window_sec"])

        banned = []
        for ip, ev in list(counters.items()):
            if st["bans"].get(ip):
                continue
            rule, hits = decide(ip, ev, cfg)
            if rule:
                if a.dry_run:
                    dur = int(cfg["ban_seconds"].get(rule, 86400))
                    banned.append((ip, rule, hits, dur))
                    continue
                dur = ban(ip, rule, hits, cfg, st)
                banned.append((ip, rule, hits, dur))
        for ip, rule, hits, dur in banned:
            log("BAN %s на %s ч - правило %s, хитов %s"
                % (ip, dur // 3600, rule, hits))

        gone = []
        if a.dry_run:
            print(json.dumps({"counters": len(counters),
                              "would_ban": banned,
                              "active_now": len(st["bans"])},
                             ensure_ascii=False, indent=2))
            return
        gone = expire(st)
        for ip in gone:
            log("UNBAN %s - срок вышел" % ip)

        write_map(st)
        write_app_blocklist(st)
        write_bans_audit(st)
        save(st)
        if a.once or a.dry_run:
            print(json.dumps({"counters": len(counters), "banned": banned,
                              "active": len(st["bans"]), "gone": gone},
                             ensure_ascii=False, indent=2))
            return
        time.sleep(int(cfg["interval"]))


def save(st):
    st["offset"] = {k: v for k, v in st["offset"].items() if os.path.exists(k.split("off:", 1)[1])}
    write_json(STATE_PATH, st)


if __name__ == "__main__":
    main()
