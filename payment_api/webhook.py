"""Доставка вебхуков мерчанту при оплате заказа.

Простая схема: 5 попыток, повторные попытки делает следующий цикл поллера
(интервал CHECK_INTERVAL). Для MVP достаточно.

Безопасность (SSRF): URL вебхука задаёт ПОЛЬЗОВАТЕЛЬ, а POST делает НАШ сервер
из своей внутренней сети. Поэтому адрес проверяется дважды - при создании заказа
и перед самой отправкой: только http/https, только публичные IP (без loopback,
приватных, link-local и метаданных облака). Редиректы не выполняются
(allow_redirects=False) - иначе проверку адреса можно было бы обойти
перебросом на 127.0.0.1.
Отдельно запрещены СВОИ публичные адреса (домены и IP сервера из
WEBHOOK_SELF_HOSTS / WEBHOOK_SELF_IPS): они публичные, общий гард их пропускает -
а в логах 18.09.2026 таких POST в собственный /phpmyadmin/ ровно 86.
"""
import contextlib
import hashlib
import hmac
import ipaddress
import json
import logging
import socket
import threading
import time
from urllib.parse import urlsplit

import requests

import config
from payment_api import events, audit

log = logging.getLogger("webhook")

# Своя сессия: trust_env=False отключает переменные окружения (http_proxy и пр.)
# - иначе прокси из окружения обошёл бы и гард, и прибитый DNS ниже.
_SESSION = requests.Session()
_SESSION.trust_env = False

# Оригинальный резолвер держим ДО подмены (см. pinned_dns).
_ORIG_GETADDRINFO = socket.getaddrinfo
_PIN = threading.local()

MAX_ATTEMPTS = 5

# Секрет подписи (X-CleanTrade-Signature). Не задан - шлём без подписи,
# но тогда получатель не может отличить наше уведомление от чужого.
WEBHOOK_SECRET = config.get("WEBHOOK_SECRET", "")

# Хосты, которые нельзя резолвить даже через DNS (имена локальной машины).
BLOCKED_HOSTS = {
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal", "metadata.goog",
}


# Наши собственные имена и адреса: публичные, поэтому общий гард их пропускает.
# Читаем КОНСТАНТЫ config, а не config.get(..., "") - иначе берётся только
# переменная окружения с пустым умолчанием и на проде списки оказываются
# пустыми (проверено живьём 18.09.2026: SELF_IPS = ['127.0.1.1']).
SELF_HOSTS = tuple(h.strip().lower() for h in
                   (getattr(config, "WEBHOOK_SELF_HOSTS", "") or "").split(",")
                   if h.strip())
SELF_IPS = set(i.strip() for i in
               (getattr(config, "WEBHOOK_SELF_IPS", "") or "").split(",")
               if i.strip())
# Плюс адреса этой машины: даже если имя не попало в список, свой интерфейс
# узнаём сами.
try:
    SELF_IPS.update(socket.gethostbyname_ex(socket.gethostname())[2])
except Exception:
    pass


def _self_ip(raw: str) -> bool:
    '''Наш ли это адрес (включая ::ffff:1.2.3.4 и десятичную запись IP).'''
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return str(ip) in SELF_IPS


def _is_our_name(host: str) -> str:
    '''Наше ли это имя (суффиксом: www.cleantrade.info тоже наш).'''
    low = (host or "").lower().rstrip(".")
    for own in SELF_HOSTS:
        if low == own or low.endswith("." + own):
            return own
    return ""


class WebhookUrlError(ValueError):
    """Адрес вебхука небезопасен или некорректен."""


def _check_host_addresses(host: str) -> list:
    """Все адреса, в которые резолвится хост, должны быть публичными.

    Возвращает список проверенных адресов - их же прибивает к имени pinned_dns,
    чтобы соединение шло ровно туда, что мы проверили (без второго резолва).
    """
    try:
        infos = _ORIG_GETADDRINFO(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookUrlError("host does not resolve: {} ({})".format(host, exc))
    if not infos:
        raise WebhookUrlError("host does not resolve: {}".format(host))

    checked = []
    for info in infos:
        raw = (info[4][0] or "").split("%")[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise WebhookUrlError("unparsable address for {}: {}".format(host, raw))
        if _self_ip(raw):
            raise WebhookUrlError(
                "host {} resolves to our own address {}".format(host, raw))
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise WebhookUrlError(
                "host {} resolves to a non-public address {}".format(host, raw)
            )
        if raw not in checked:
            checked.append(raw)
    return checked


def _pinned_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """getaddrinfo, ограниченный адресами из pinned_dns (в остальных потоках - обычный)."""
    pins = getattr(_PIN, "hosts", None)
    if pins:
        ips = pins.get((host or "").lower().rstrip("."))
        if ips:
            out = []
            for ip in ips:
                fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
                if family not in (0, fam):
                    continue
                addr = (ip, port) if fam == socket.AF_INET else (ip, port, 0, 0)
                out.append((fam, type or socket.SOCK_STREAM,
                            proto or socket.IPPROTO_TCP, "", addr))
            if out:
                return out
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)


@contextlib.contextmanager
def pinned_dns(host, ips):
    """Прибить имя к уже проверенным адресам на время запроса.

    Гард резолвит имя отдельно, а соединение резолвит его ВТОРОЙ раз - и между
    двумя резолвами имя можно перебросить на 127.0.0.1 (DNS rebinding; ровно
    этот приём в логах атаки 18.09.2026). С прибитым DNS второго резолва нет:
    соединение идёт только на проверенные адреса.
    """
    prev = getattr(_PIN, "hosts", None)
    _PIN.hosts = {(host or "").lower().rstrip("."): list(ips or [])}
    try:
        yield
    finally:
        _PIN.hosts = prev


# Подменяем резолвер один раз на процесс; без активного pinned_dns он ведёт себя
# как системный (это проверено тестом).
socket.getaddrinfo = _pinned_getaddrinfo


def validate_url(url: str) -> str:
    """Проверить URL вебхука. Возвращает очищенный URL, иначе WebhookUrlError.

    Гард от SSRF: наш сервер сам делает POST по этому адресу, поэтому
    внутренние адреса (127.0.0.1, x.x.x.x, 10/172/192.168, ::1)
    запрещены так же, как схемы кроме http/https.
    """
    url = (url or "").strip()
    if not url:
        return ""

    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise WebhookUrlError("only http:// and https:// are allowed")
    host = parts.hostname or ""
    if not host:
        raise WebhookUrlError("no host in url")
    if host.lower() in BLOCKED_HOSTS:
        raise WebhookUrlError("host is not allowed: {}".format(host))
    if _is_our_name(host):
        raise WebhookUrlError("host is our own address: {}".format(host))
    _check_host_addresses(host)
    return url


def payload_for(order: dict) -> dict:
    payload = {
        "order_id": order["id"],
        "product_id": order.get("product_id"),
        "amount": order.get("amount"),
        "wallet": order.get("wallet") or "",
        "status": order.get("status"),
        "tx_id": order.get("tx_id"),
        "paid_at": order.get("paid_at"),
        "customer_ref": order.get("customer_ref") or "",
    }
    # Для заказов тарифов (product_id = plan:<тариф>) отдаём ключ подписчика.
    product_id = order.get("product_id") or ""
    if product_id.startswith("plan:") and order.get("api_key"):
        payload["plan"] = product_id.split(":", 1)[1]
        payload["api_key"] = order["api_key"]
    return payload


def secret_for_order(order: dict) -> str:
    """Секрет подписи ИМЕННО этого мерчанта, общий - только запасной путь.

    У каждой записи подписчика свой webhook_secret (payment_api/subscribers.py):
    один секрет на сервис означал, что мерчант, знающий его, может подделать
    вебхук другому. Общий секрет остаётся для заказов мастер-ключа и для
    записей, созданных до 22.10.2026 (их добивает backfill при старте).
    """
    owner_id = order.get("owner_id") or ""
    if owner_id:
        try:
            from payment_api import subscribers
            own = subscribers.secret_of_owner(owner_id)
        except Exception:
            own = ""
        if own:
            return own
    return WEBHOOK_SECRET


def signature(payload: dict, timestamp: str, secret: str = "") -> str:
    """HMAC-SHA256 от "<timestamp>.<тело JSON>" - так подпись заодно
    закрывает и повтор чужой доставки (получатель сравнивает время).

    secret не задан - берётся общий WEBHOOK_SECRET (старое поведение).
    """
    secret = secret or WEBHOOK_SECRET
    if not secret:
        return ""
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    mac = hmac.new(secret.encode("utf-8"),
                   timestamp.encode("ascii") + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def send(order: dict) -> bool:
    url = (order.get("webhook_url") or "").strip()
    if not url:
        return False
    try:
        url = validate_url(url)
    except WebhookUrlError as exc:
        log.warning("webhook url rejected (%s): %s", url, exc)
        events.log_event("webhook_fail", order_id=order["id"], url=url[:200],
                         error="unsafe url: {}".format(exc))
        return False
    # Второй резолв перед отправкой: имя могли перебросить уже ПОСЛЕ validate_url.
    # Проверенные адреса прибиваем к имени на время запроса (pinned_dns).
    host = urlsplit(url).hostname or ""
    try:
        pinned = _check_host_addresses(host)
    except WebhookUrlError as exc:
        log.warning("webhook url rejected on send (%s): %s", url, exc)
        events.log_event("webhook_fail", order_id=order["id"], url=url[:200],
                         error="unsafe url on send: {}".format(exc))
        return False
    payload = payload_for(order)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "CleanTrade-Webhook/1.0"}
    ts = str(int(time.time()))
    secret = secret_for_order(order)
    sig = signature(payload, ts, secret)
    if sig:
        headers["X-CleanTrade-Timestamp"] = ts
        headers["X-CleanTrade-Signature"] = sig
        if secret == WEBHOOK_SECRET:
            log.info("webhook %s: подпись общим секретом (у заказа нет владельца)",
                     order["id"])
    else:
        log.warning("WEBHOOK_SECRET не задан - вебхук уходит без подписи")
    t0 = time.time()
    try:
        with pinned_dns(host, pinned):
            resp = _SESSION.post(url, data=body, headers=headers, timeout=10,
                                 allow_redirects=False)
        ok = 200 <= resp.status_code < 300
        if not ok:
            log.warning("webhook %s -> HTTP %s (%s)", url, resp.status_code, resp.text[:200])
            events.log_event("webhook_fail", order_id=order["id"], url=url,
                              status=resp.status_code)
        else:
            events.log_event("webhook_ok", order_id=order["id"], url=url,
                              status=resp.status_code)
        audit.log(order["id"], "webhook", attempt=int(order.get("webhook_attempts") or 0) + 1,
                  ok=ok, status=resp.status_code, url=url[:200],
                  duration_ms=int((time.time() - t0) * 1000))
        return ok
    except Exception as exc:
        log.error("webhook error %s: %s", url, exc)
        events.log_event("webhook_fail", order_id=order["id"], url=url, error=str(exc))
        audit.log(order["id"], "webhook", attempt=int(order.get("webhook_attempts") or 0) + 1,
                  ok=False, status=None, url=url[:200], error=str(exc)[:300],
                  duration_ms=int((time.time() - t0) * 1000))
        return False
