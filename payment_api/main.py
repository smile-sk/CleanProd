"""CleanTrade Payment API - приём USDT (TRC20) по уникальным суммам.

Фазы:
  1. MVP: один мерчант (мастер-ключ из .env), заказы, поллер, вебхуки.
  2. Собака ест свой корм: CleanTrail продаётся через этот же API.
  3. Биллинг: подписчики (free/starter/pro/business), ключи sp_live_...,
     лимиты заказов/мес (402 Payment Required), оплата тарифа через API
     (POST /v1/subscribe -> заказ plan:<тариф> -> вебхук с api_key).

Free-тариф (цена 0) платить нечем - ключ выдаётся сразу в ответе на
POST /v1/subscribe, поллер в этой ветке не участвует. Раньше free-заказ
уходил в ожидание перевода «0 USDT» и просто истекал: до 2026-09-16 не было
выдано ни одного бесплатного ключа.

Запуск (локально, dev):
    uvicorn payment_api.main:app --port 8080
"""
import asyncio
import hmac
import ipaddress
import json
import time
from decimal import Decimal
from typing import Optional, Tuple
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

import config
from payment_api import (orders, webhook, subscribers, admin, events, tgauth,
                         tgbot, guard, audit)
from payment_api.walletaddr import WalletAddressError, validate_trc20
from core.payment import PaymentDetector, match_transfer



class CodedError(HTTPException):
    """HTTPException с машинным кодом причины.

    Страница подписки должна не показывать сырую английскую строку, а
    предлагать шаг («открой бота и нажми Start»). Для этого клиенту нужен
    стабильный код, а не текст, который можно переписать.
    """

    def __init__(self, status_code: int, detail: str, code: str):
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
log = logging.getLogger("stablepay")

API_VERSION = config.API_VERSION  # единый источник версии (config.py / .env)
API_KEY = config.get("PAYMENT_API_KEY", "")
FREE_IP_DAILY_LIMIT = config.get_int("FREE_IP_DAILY_LIMIT", 5)
SUBSCRIBE_IP_DAILY_LIMIT = config.get_int("SUBSCRIBE_IP_DAILY_LIMIT", 30)
SUPPORT_SECRET = config.get("SUPPORT_SHARED_SECRET", "")
FREE_TG_IP_FLAG = config.get_int("FREE_TG_IP_FLAG", 3)
RATE_SUBSCRIBE_PER_MIN = config.RATE_SUBSCRIBE_PER_MIN
RATE_SUBSCRIBE_PER_HOUR = config.RATE_SUBSCRIBE_PER_HOUR
RATE_TG_PER_MIN = config.RATE_TG_PER_MIN
RATE_API_PER_MIN = config.RATE_API_PER_MIN
RATE_PUBLIC_PER_MIN = config.RATE_PUBLIC_PER_MIN
RATE_ADMIN_PER_MIN = config.RATE_ADMIN_PER_MIN
MAX_PENDING_PER_IP = config.MAX_PENDING_PER_IP

DETECTOR = PaymentDetector(
    wallet=config.TRX_WALLET,
    api_key=config.TRONSCAN_API_KEY,
    mock=config.USE_MOCK,
    min_interval=config.TRON_MIN_INTERVAL,   # лимит TronScan: 1 запрос/с на ключ
    cache_ttl=config.TRON_CACHE_TTL,         # один запрос на кошелёк за цикл
)


class OrderRequest(BaseModel):
    """Заказ: ОБЯЗАТЕЛЬНЫЙ кошелёк получателя + сумма.

    Кошелёк - часть КАЖДОГО заказа, а не настройка аккаунта: один и тот же
    адрес можно присылать в каждом заказе, можно разные. Уникальность суммы
    считается ВНУТРИ одного кошелька (orders.used_amounts), поэтому одинаковые
    суммы на разных кошельках - норма: мы смотрим конкретную сумму на
    конкретном адресе.

    Модель строгая (extra="forbid"): лишнее поле - 422, а не «проглотить молча»;
    текстовые поля без управляющих символов (перевод строки в поле ломал бы
    журнал); сумма - Decimal, не больше двух знаков после точки.
    """
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(..., min_length=1, max_length=64,
                            description="Ваш идентификатор товара/услуги")
    wallet: str = Field(..., min_length=34, max_length=34,
                        description="TRC20-адрес получателя (34 символа, начинается с T)")
    amount: Optional[Decimal] = Field(
        default=None, gt=0, le=Decimal("1000000"), max_digits=12, decimal_places=2,
        description="Своя сумма (до копеек). Не задано - генерируется уникальная",
    )
    webhook_url: Optional[str] = Field(
        default=None, max_length=512,
        description="URL для уведомления об оплате (POST, JSON)",
    )
    customer_ref: Optional[str] = Field(
        default=None, max_length=64,
        description="Ваш идентификатор покупателя (без переводов строк)",
    )

    @field_validator("product_id", "customer_ref")
    @classmethod
    def _clean_text(cls, value):
        """Мусор и переводы строк в текстовых полях - отказ, а не «как-нибудь»."""
        if value is None:
            return value
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("control characters are not allowed")
        return value.strip()

    @field_validator("product_id")
    @classmethod
    def _product_not_blank(cls, value):
        if not value:
            raise ValueError("product_id must not be blank")
        return value


class TgConfirmRequest(BaseModel):
    code: str = Field(..., min_length=8, max_length=64,
                      description="Код из POST /v1/tg/start")
    tg_id: int = Field(..., description="Числовой Telegram id (от бота, не от сайта)")
    username: Optional[str] = Field(default=None, max_length=64)
    first_name: Optional[str] = Field(default=None, max_length=64)


class MyKeyRequest(BaseModel):
    tg_code: str = Field(..., min_length=8, max_length=64,
                         description="Подтверждённый код Telegram")


class SubscribeRequest(BaseModel):
    plan: str = Field(..., min_length=2, max_length=20,
                      description="free|starter|pro|business|*_yearly")
    tg_code: Optional[str] = Field(
        default=None, min_length=8, max_length=64,
        description="Код из POST /v1/tg/start, подтверждённый кнопкой Start в боте",
    )
    customer_ref: Optional[str] = Field(
        default=None, min_length=2, max_length=64,
        description="Легаси-путь без Telegram (email/@username или tg:<id>)",
    )
    renew: bool = Field(
        default=False,
        description="true - платить за продление, даже если тариф уже активен",
    )
    webhook_url: Optional[str] = Field(
        default=None, max_length=512,
        description="URL, куда придёт api_key после оплаты",
    )


# --- Кто на самом деле пришёл: IP клиента за Cloudflare + Apache ---
# Наша цепочка: клиент -> Cloudflare -> Apache (127.0.0.1) -> это приложение.
# X-Forwarded-For может прислать САМ КЛИЕНТ, а Apache лишь дописывает реальный
# адрес в конец. Поэтому первое значение заголовка подделывается: проверено
# живьём 16.09.2026 - заголовок "X-Forwarded-For: 1.2.3.4" попал в журнал как
# IP клиента, а значит лимит Free по IP (5/сутки) обходился сменой одной строки.
# Теперь доверяем только тому, что дописал наш Apache:
#   * Apache стоит МЕЖДУ Cloudflare и приложением, поэтому сосед приложения -
#     он сам (127.0.0.1), а не Cloudflare. Кто именно пришёл, видно по ХВОСТУ
#     X-Forwarded-For: Apache дописывает туда того, кто к нему подключился.
#   * хвост - адрес Cloudflare -> запрос шёл через CF, значит настоящий клиент
#     лежит в CF-Connecting-IP (свой такой заголовок прислать нельзя: CF
#     отвечает 403 - проверено живьём 16.09.2026);
#   * хвост - обычный адрес -> запрос пришёл на origin напрямую, берём его
#     (левый CF-Connecting-IP в этом случае игнорируется);
#   * Apache ничего не дописал -> адрес прямого соединения.
CLOUDFLARE_NETS = tuple(ipaddress.ip_network(c) for c in (
    # IPv4 (https://www.cloudflare.com/ips-v4)
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20",
    "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13",
    "131.0.72.0/22",
    # IPv6 (https://www.cloudflare.com/ips-v6)
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
))


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(str(value or "").strip().strip("[]"))
    except ValueError:
        return None


def _from_cloudflare(value: str) -> bool:
    ip = _parse_ip(value)
    return bool(ip) and any(ip in net for net in CLOUDFLARE_NETS)


def _client_ip(request: Optional[Request]) -> str:
    """IP клиента за Cloudflare + Apache - для журнала и лимитов Free."""
    if request is None:
        return ""
    peer = (request.client.host if request.client else "") or ""
    fwd = request.headers.get("x-forwarded-for") or ""
    parts = [x.strip() for x in fwd.split(",") if x.strip()]
    last = parts[-1] if parts and _parse_ip(parts[-1]) else ""
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if last and _from_cloudflare(last) and _parse_ip(cf):
        return cf[:64]
    if last:
        return last[:64]
    if _from_cloudflare(peer) and _parse_ip(cf):
        return cf[:64]
    real = (request.headers.get("x-real-ip") or "").strip()
    if _parse_ip(real):
        return real[:64]
    return peer[:64]


def _check_webhook(url: Optional[str]) -> str:
    """Проверить адрес вебхука до создания заказа (гард от SSRF)."""
    try:
        return webhook.validate_url(url or "")
    except webhook.WebhookUrlError as exc:
        events.log_event("webhook_rejected", url=(url or "")[:200], error=str(exc))
        raise HTTPException(status_code=400,
                            detail="invalid webhook_url: {}".format(exc))


def _subscribe_ip_guard(ip: str) -> None:
    """Потолок заявок с одной сети на публичной двери /v1/subscribe.

    Free-лимит считает только бесплатные выдачи, поэтому платные заявки с
    вебхуком шли мимо него - а через вебхук наш сервер делает POST наружу.
    Считаем СЕТЬ (IPv6 /64, IPv4 /24), иначе ротация адресов обходит лимит.
    """
    if SUBSCRIBE_IP_DAILY_LIMIT <= 0:
        return
    allowed, used = subscribers.subscribe_ip_allow(ip, SUBSCRIBE_IP_DAILY_LIMIT)
    if not allowed:
        events.log_event("subscribe_limited", ip=ip, used=used,
                         limit=SUBSCRIBE_IP_DAILY_LIMIT)
        raise HTTPException(
            status_code=429,
            detail="too many subscribe requests from your network today "
                   "({} per day)".format(SUBSCRIBE_IP_DAILY_LIMIT),
        )


def _check_support_secret(value: Optional[str],
                          request: Optional[Request] = None) -> None:
    """Общий секрет платёжки и бота поддержки (заголовок X-Support-Secret).

    Без него эти эндпоинты отвечают 503, а не «пускают всех»: подтвердить
    чужой код или вытащить чужой ключ снаружи нельзя.

    Отказ пишется в ленту вместе с ip и path: до 02.11.2026 в событии был
    только reason="bad_secret", и по 547 таким строкам нельзя было понять,
    кто и в какую дверь стучался.
    """
    if not SUPPORT_SECRET:
        raise HTTPException(status_code=503,
                            detail="support secret is not configured on the server")
    if not value or not hmac.compare_digest(str(value), SUPPORT_SECRET):
        events.log_event("support_auth_fail", reason="bad_secret",
                         ip=_client_ip(request),
                         path=(request.url.path if request is not None else ""))
        raise HTTPException(status_code=403, detail="bad support secret")


def _plan_alive(sub: dict) -> bool:
    """Тариф ещё не истёк (нет даты - считаем живым: free и легаси-записи)."""
    return not subscribers.is_expired(sub)


def _identity(req: SubscribeRequest) -> Tuple[str, Optional[int], str]:
    """Кто клиент: подтверждённый Telegram (tg_code) или легаси customer_ref.

    tg_code обязан быть confirmed - то есть человек реально нажал Start в боте
    и Telegram подписал, что этот tg_id настоящий. Выдумать его нельзя.
    """
    if req.tg_code:
        rec = tgauth.get(req.tg_code)
        if not rec:
            raise CodedError(404, "telegram code not found", "tg_code_not_found")
        if rec.get("status") not in (tgauth.STATUS_CONFIRMED,
                                     tgauth.STATUS_USED) or not rec.get("tg_id"):
            raise CodedError(
                401, "telegram not confirmed - open the link and press Start",
                "tg_not_confirmed")
        tg_id = int(rec["tg_id"])
        if rec.get("status") == tgauth.STATUS_USED:
            # Код уже отработал. Страница держит один и тот же код, пока человек
            # на ней (повторное нажатие кнопки, перезагрузка), поэтому жёсткий
            # отказ сломал бы обычное второе нажатие. Но повтор видно в ленте -
            # чтобы он не выглядел как новый клиент.
            events.log_event("tg_code_reused", tg_id=tg_id,
                             code=req.tg_code[:8],
                             uses=int(rec.get("uses", 0)))
        tgauth.mark_used(req.tg_code)
        events.log_event("subscribe_tg", tg_id=tg_id,
                         username=rec.get("username") or "")
        return "tg:{}".format(tg_id), tg_id, rec.get("username") or ""
    ref = (req.customer_ref or "").strip()
    if not ref:
        raise HTTPException(status_code=400,
                            detail="tg_code required (or legacy customer_ref)")
    events.log_event("subscribe_legacy", customer_ref=ref)
    return ref, None, ""


def _reused_payload(plan_id: str, sub: dict, free: bool = False) -> dict:
    """Ответ «ключ у вас уже есть» - чтобы повторная заявка не стоила денег."""
    plan = subscribers.PLANS.get(plan_id, {})
    return {
        "order_id": None,
        "product_id": "plan:" + plan_id,
        "amount": plan.get("price", 0.0),
        "wallet": config.TRX_WALLET,
        "status": "paid",
        "created_at": sub.get("created_at"),
        "customer_ref": sub.get("customer_ref") or "",
        "api_key": sub["key"],
        "webhook_secret": sub.get("webhook_secret") or "",
        "plan": plan_id,
        "plan_name": plan.get("name"),
        "plan_price": plan.get("price", 0.0),
        "plan_days": plan.get("days", 30),
        "plan_expires_at": sub.get("plan_expires_at"),
        "tg_id": sub.get("tg_id"),
        "tg_username": sub.get("tg_username") or "",
        "verified": bool(sub.get("verified")),
        "free": free,
        "reused": True,
    }


def _flag_multi_tg(ip: str, tg_id) -> None:
    """Метка, а не блокировка: несколько разных Telegram с одного адреса.

    Жёстко резать по IP нельзя - у мобильных операторов один адрес на тысячи
    абонентов, и «один IP = один клиент» ударил бы по живым людям. Поэтому
    пишем событие: видно в ленте админки, разбирается человеком.
    """
    if not ip or not tg_id or FREE_TG_IP_FLAG <= 0:
        return
    count = subscribers.free_ip_tg_count(ip)
    if count > FREE_TG_IP_FLAG:
        events.log_event("free_multi_tg", ip=ip, tg_id=int(tg_id), tg_count=count)


def _notify_key(sub: Optional[dict], free: bool = False, order_id: str = "") -> bool:
    """Отдать ключ человеку в Telegram - это и есть подтверждение подписки.

    Раньше единственным каналом была страница на сайте: закрыл вкладку -
    ключа нет. Теперь ключ приходит в бот, откуда его не потерять.
    """
    if not sub or not sub.get("tg_id"):
        return False
    plan = subscribers.PLANS.get(sub.get("plan"), {})
    text = tgbot.key_message(sub["key"], plan.get("name", sub.get("plan") or ""),
                             sub.get("plan_expires_at") or "",
                             webhook_secret=sub.get("webhook_secret") or "")
    if free:
        text = "🎉 <b>Free plan activated</b>\n\n" + text
    else:
        text = "✅ <b>Payment received</b>\n\n" + text
    ok = tgbot.send(sub["tg_id"], text)
    events.log_event("key_delivered_tg" if ok else "key_delivery_failed",
                     tg_id=int(sub["tg_id"]), plan=sub.get("plan"), order_id=order_id)
    return ok


def _resolve_key(x_api_key: Optional[str], ip: str = "") -> Tuple[str, Optional[dict]]:
    """("master", None) для нашего ключа из .env, ("sub", sub) для подписчика."""
    if not x_api_key:
        events.log_event("auth_fail", reason="missing", ip=ip)
        raise HTTPException(status_code=401, detail="missing api key")
    if API_KEY and x_api_key == API_KEY:
        return "master", None
    sub = subscribers.get_by_key(x_api_key)
    if not sub:
        events.log_event("auth_fail", reason="invalid", key=x_api_key[:12], ip=ip)
        raise HTTPException(status_code=401, detail="invalid api key")
    if sub.get("status") == "active" and subscribers.is_expired(sub):
        # Срок тарифа вышел. Раньше plan_expires_at только ПИСАЛОСЬ, а при
        # проверке ключа дата не читалась вообще: подписка не истекала никогда.
        subscribers.mark_expired(x_api_key)
        events.log_event("auth_fail", reason="expired", key=x_api_key[:12], ip=ip,
                         plan=sub.get("plan"),
                         expired_at=sub.get("plan_expires_at") or "")
        raise HTTPException(
            status_code=401,
            detail="subscription expired on {} - renew at "
                   "https://pay.cleantrade.info/subscribe.html".format(
                       (sub.get("plan_expires_at") or "")[:10]),
        )
    if sub.get("status") != "active":
        events.log_event("auth_fail", reason="inactive", key=x_api_key[:12], ip=ip,
                         status=sub.get("status") or "")
        raise HTTPException(status_code=401, detail="invalid api key")
    return "sub", sub


def _order_ttl(order: dict) -> int:
    """Сколько секунд заказ ждёт оплату: у тарифов свой, короткий срок.

    Тарифный заказ держит уникальную сумму (детект платежа - по сумме),
    поэтому брошенная заявка на Starter блокировала бы Starter всем.
    """
    if (order.get("product_id") or "").startswith("plan:"):
        return config.PLAN_ORDER_TTL
    return config.ORDER_EXPIRATION_SECONDS


def _order_payload(order: dict) -> dict:
    ttl = _order_ttl(order)
    expires_at = ""
    try:
        created = datetime.fromisoformat(order["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expires_at = (created + timedelta(seconds=ttl)).isoformat(timespec="seconds")
    except Exception:
        pass
    return {
        "order_id": order["id"],
        "product_id": order["product_id"],
        "amount": order["amount"],
        "wallet": order["wallet"],
        "status": order["status"],
        "created_at": order["created_at"],
        "ttl_seconds": ttl,
        "expires_at": expires_at,
    }


def _plan_payload(order: dict, plan_id: str, **extra) -> dict:
    plan = subscribers.get_plan(plan_id) or {}
    payload = _order_payload(order)
    payload.update({
        "plan": plan_id,
        "plan_name": plan.get("name"),
        "plan_price": plan.get("price", 0.0),
        "plan_days": plan.get("days", 30),
    })
    payload.update(extra)
    return payload


def _activate_plan_order(order: dict) -> Optional[dict]:
    """Активировать тариф по оплаченному заказу plan:<тариф> и привязать ключ."""
    product_id = order.get("product_id") or ""
    if not product_id.startswith("plan:"):
        return None
    plan_id = product_id.split(":", 1)[1]
    customer_ref = order.get("customer_ref") or ""
    if not customer_ref or not subscribers.get_plan(plan_id):
        return None
    sub = subscribers.activate_plan(customer_ref, plan_id,
                                    tg_id=order.get("tg_id"),
                                    tg_username=order.get("tg_username") or "",
                                    ip=order.get("ip") or "")
    orders.set_api_key(order["id"], sub["key"])
    orders.set_owner_id(order["id"], sub["id"])
    _notify_key(sub, order_id=order["id"])
    log.info("Тариф %s активирован для %s (ключ %s...)", plan_id, customer_ref, sub["key"][:12])
    return sub


def _free_subscribe(plan_id: str, customer_ref: str, webhook_url: str, ip: str,
                    tg_id=None, tg_username: str = "") -> dict:
    """Free: платить нечего - активируем сразу и отдаём ключ в ответе.

    Правило по Telegram жёсткое: один tg_id - один бесплатный ключ навсегда.
    Раньше личностью был вписанный руками адрес, поэтому пять адресов давали
    пять ключей; теперь адрес личностью не является.
    """
    if not tg_id:
        # Легаси-путь (customer_ref без Telegram) выдавал ключ вообще без
        # личности: 41 699 вызовов и 220 бесплатных ключей за сутки 17-18.09
        # 2026, потому что придуманный ref ничем не проверяется. Free теперь
        # только по подтверждённому Telegram: его tg_id подписан Telegram,
        # выдумать его нельзя.
        events.log_event("free_requires_tg", customer_ref=customer_ref, ip=ip)
        raise HTTPException(
            status_code=403,
            detail="free plan requires a linked Telegram - open the page and "
                   "press «Link Telegram»",
        )

    existing = subscribers.get_by_tg(tg_id) if tg_id else None
    if existing is None:
        existing = subscribers.get_by_customer_ref(customer_ref)
    if existing and existing.get("status") == "active":
        if existing.get("plan") != plan_id:
            raise HTTPException(
                status_code=409,
                detail="you already have an active '{}' plan - free is not a downgrade".format(
                    existing.get("plan")),
            )
        # повторный запрос того же клиента - отдаём тот же ключ, дублей не плодим
        return {
            "order_id": None,
            "product_id": "plan:" + plan_id,
            "amount": 0.0,
            "wallet": config.TRX_WALLET,
            "status": "paid",
            "created_at": existing.get("created_at"),
            "customer_ref": existing.get("customer_ref") or customer_ref,
            "api_key": existing["key"],
            "webhook_secret": existing.get("webhook_secret") or "",
            "plan": plan_id,
            "plan_name": subscribers.PLANS[plan_id]["name"],
            "plan_price": 0.0,
            "plan_days": subscribers.PLANS[plan_id]["days"],
            "plan_expires_at": existing.get("plan_expires_at"),
            "free": True,
            "reused": True,
            "tg_id": existing.get("tg_id"),
            "tg_username": existing.get("tg_username") or "",
            "verified": bool(existing.get("verified")),
        }

    if ip and FREE_IP_DAILY_LIMIT > 0:
        allowed, used = subscribers.free_ip_allow(ip, FREE_IP_DAILY_LIMIT)
        if not allowed:
            events.log_event("free_limited", ip=ip, customer_ref=customer_ref, used=used)
            raise HTTPException(
                status_code=429,
                detail="free plan limit for this IP reached ({} per day) - "
                       "try tomorrow or pick a paid plan".format(FREE_IP_DAILY_LIMIT),
            )

    order = orders.create_order(0.0, "plan:" + plan_id, webhook_url,
                                config.TRX_WALLET, customer_ref,
                                tg_id=tg_id, tg_username=tg_username, ip=ip,
                                ip_prefix=subscribers.subnet_key(ip))
    orders.mark_paid(order["id"], "free:no-payment")
    sub = subscribers.activate_plan(customer_ref, plan_id, tg_id=tg_id,
                                    tg_username=tg_username, ip=ip)
    orders.set_api_key(order["id"], sub["key"])
    orders.set_owner_id(order["id"], sub["id"])
    subscribers.free_ip_register(ip, tg_id=tg_id)
    subscribers.subscribe_ip_register(ip)
    _flag_multi_tg(ip, tg_id)
    _notify_key(sub, free=True, order_id=order["id"])
    log.info("Free-тариф активирован для %s (ключ %s...), ip=%s",
             customer_ref, sub["key"][:12], ip or "-")

    payload = _plan_payload(orders.get_order(order["id"]) or order, plan_id,
                            api_key=sub["key"],
                            webhook_secret=sub.get("webhook_secret") or "",
                            free=True, reused=False,
                            plan_expires_at=sub.get("plan_expires_at"),
                            tg_id=sub.get("tg_id"),
                            tg_username=sub.get("tg_username") or "",
                            verified=bool(sub.get("verified")))
    return payload


async def _deliver_webhooks() -> None:
    """Доставка/повторы вебхуков для оплаченных заказов.

    Раньше после пятой неудачной попытки заказ просто исчезал из очереди, и
    узнавали мы об этом никогда. Теперь последняя неудача пишется в журнал
    доказательств и в ленту событий - «оплата была, вебхук не дошёл» это наш
    риск, о котором мы обязаны знать.
    """
    for order in orders.list_pending_webhook():
        attempts = orders.bump_webhook_attempts(order["id"])
        if not isinstance(attempts, int):
            attempts = int(order.get("webhook_attempts") or 0) + 1
        if webhook.send(order):
            orders.mark_webhook_delivered(order["id"])
            log.info("Webhook доставлен: %s", order["id"])
            continue
        if attempts >= webhook.MAX_ATTEMPTS:
            audit.log(order["id"], "webhook_giveup", attempts=attempts,
                      url=(order.get("webhook_url") or "")[:200])
            events.log_event("webhook_giveup", order_id=order["id"],
                             attempts=attempts)
            log.error("Вебхук НЕ доставлен после %s попыток: заказ %s (оплата есть, "
                      "уведомления у мерчанта нет)", attempts, order["id"])


def _order_created_ts(order: dict, skew: float = 60.0) -> float:
    """Момент создания заказа в секундах эпохи (минус запас на расхождение часов).

    Перевод, пришедший ДО заказа, оплатой этого заказа не считается: иначе
    старый перевод с той же суммой (в истории кошелька он висит годами -
    например 19.889 USDT от 24.05.2026) закрывал бы каждый новый заказ сразу.
    """
    try:
        created = datetime.fromisoformat(order["created_at"])
    except (KeyError, ValueError):
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created.timestamp() - skew


async def _poll_once_inner() -> None:
    """Один проход поллера: вебхуки, потом проверка ожидающих оплат.

    TronScan опрашиваем ПО КОШЕЛЬКУ, а не по заказу: у них лимит 1 запрос
    в секунду на ключ, и прежняя схема «запрос на каждый заказ» сама себя
    блокировала (22.10.2026: с 12:51 до 13:40 все ответы 429, детект стоял).
    """
    await _deliver_webhooks()

    waiting = orders.list_waiting()
    if not waiting:
        return
    loop = asyncio.get_running_loop()
    now = datetime.now(timezone.utc)
    # Один перевод = один заказ: tx_id, уже привязанный к заказу, второй раз не
    # зачитываем - иначе два заказа на одну сумму оба стали бы paid.
    used_tx = orders.used_tx_ids()

    by_wallet: dict = {}
    for order in waiting:
        # Заказ «на 0 USDT» ждать нечего: перевода нуля не бывает.
        if orders.money(order.get("amount")) <= 0:
            orders.mark_expired(order["id"], reason="zero_amount")
            log.info("Заказ %s закрыт: нулевая сумма", order["id"])
            continue
        try:
            created = datetime.fromisoformat(order["created_at"])
        except (KeyError, ValueError):
            # Битая дата = заказ не истечёт НИКОГДА и держит сумму навсегда.
            # Раньше здесь стоял continue - «тихо живём»; закрываем с причиной.
            orders.mark_expired(order["id"], reason="bad_created_at")
            log.warning("Заказ %s закрыт: не читается created_at", order["id"])
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if (now - created).total_seconds() > _order_ttl(order):
            orders.mark_expired(order["id"], reason="timeout")
            log.info("Заказ %s истёк", order["id"])
            continue
        wallet = order.get("wallet") or config.TRX_WALLET
        by_wallet.setdefault(wallet, []).append(order)

    for wallet, group in by_wallet.items():
        if DETECTOR.mock:
            transfers = [{"tx_id": "mock:" + o["id"], "amount": float(o["amount"]),
                          "ts": time.time()} for o in group]
        else:
            transfers = await loop.run_in_executor(None, DETECTOR.transfers, wallet)
        if transfers is None:
            # Сеть или лимит: причина уже в журнале, заказы проверим в след. цикле.
            audit.scan_error(wallet, error=getattr(DETECTOR, "last_error", ""),
                             pending=len(group))
            continue
        audit.scan(wallet, pending=len(group), transfers=len(transfers),
                   source=getattr(DETECTOR, "last_source", ""))
        group.sort(key=lambda o: o.get("created_at") or "")
        for order in group:
            tx_id = match_transfer(transfers, orders.money(order["amount"]),
                                   since_ts=_order_created_ts(order), used_tx=used_tx)
            if not tx_id:
                continue
            paid_order = orders.mark_paid(order["id"], tx_id)
            if paid_order:
                used_tx.add(tx_id)
                transfer = next((t for t in transfers if t.get("tx_id") == tx_id), {})
                audit.log(order["id"], "matched", tx_id=tx_id, wallet=wallet,
                          expected_amount=order.get("amount"),
                          tx_amount=transfer.get("amount"), tx_ts=transfer.get("ts"),
                          source=getattr(DETECTOR, "last_source", ""),
                          delay_s=round(time.time() - _order_created_ts(order, 0.0), 1))
                log.info("Оплата: заказ %s, %s USDT, кошелёк %s, tx=%s",
                         paid_order["id"], paid_order["amount"], wallet, tx_id)
                _activate_plan_order(paid_order)

    await _grace_scan()


_LAST_GRACE_CHECK = 0.0
GRACE_INTERVAL = 600  # сек: позднюю оплату ищем раз в 10 минут (беречь лимит TronScan)


async def _grace_scan() -> None:
    """Поздняя оплата тарифа: заказ истёк, а перевод пришёл - оживляем заказ.

    Ходим в TronScan редко и ПО КОШЕЛЬКУ (один запрос на адрес), только по
    суммам, свободным от живых заказов НА ЭТОМ кошельке: если сумма уже
    переиспользована новым заказом, разбирается человек, а не автоматика
    (иначе можно выдать тариф не тому).
    """
    global _LAST_GRACE_CHECK
    now_ts = time.time()
    if now_ts - _LAST_GRACE_CHECK < GRACE_INTERVAL:
        return
    _LAST_GRACE_CHECK = now_ts

    late_orders = orders.list_grace(config.PLAN_PAYMENT_GRACE)
    if not late_orders:
        return
    used_tx = orders.used_tx_ids()
    loop = asyncio.get_running_loop()

    by_wallet: dict = {}
    for order in late_orders:
        wallet = order.get("wallet") or config.TRX_WALLET
        by_wallet.setdefault(wallet, []).append(order)

    # Не больше пяти кошельков за проход: это редкие запросы, бережём лимит.
    for wallet, group in list(by_wallet.items())[:5]:
        busy = orders.used_amounts(wallet)
        transfers = await loop.run_in_executor(None, DETECTOR.transfers, wallet, True)
        if transfers is None:
            continue
        group.sort(key=lambda o: o.get("created_at") or "")
        for order in group[:5]:
            if order["amount"] in busy:
                continue
            tx_id = match_transfer(transfers, orders.money(order["amount"]),
                                   since_ts=_order_created_ts(order), used_tx=used_tx)
            if not tx_id:
                continue
            paid = orders.mark_paid_late(order["id"], tx_id)
            if paid:
                transfer = next((t for t in transfers if t.get("tx_id") == tx_id), {})
                audit.log(order["id"], "matched", tx_id=tx_id, wallet=wallet,
                          expected_amount=order.get("amount"),
                          tx_amount=transfer.get("amount"), tx_ts=transfer.get("ts"),
                          late=True, source=getattr(DETECTOR, "last_source", ""))
                log.info("Поздняя оплата: заказ %s (%s USDT) - заказ оживлён",
                         paid["id"], paid["amount"])
                used_tx.add(tx_id)
                _activate_plan_order(paid)


async def _poller_loop() -> None:
    log.info("Поллер запущен: интервал %s c, mock=%s", config.CHECK_INTERVAL, config.USE_MOCK)
    while True:
        try:
            await _poll_once()
        except Exception as exc:
            log.exception("Ошибка в цикле поллера")
            events.log_event("poller_error", error=str(exc))
        await asyncio.sleep(config.CHECK_INTERVAL)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_dirs()
    # У записей, созданных до 22.10.2026, нет своего секрета подписи: без
    # этого шага они подписывались бы общим секретом сервиса - то есть один
    # мерчант мог бы подделать вебхук другому. Разово при старте.
    filled = subscribers.backfill_webhook_secrets()
    if filled:
        log.info("Секрет подписи выдан %s подписчикам (backfill)", filled)
    # Пульс пишем ДО первого цикла: иначе между стартом сервиса и первым
    # проходом файла нет, и сторож честно кричит «детект мёртв» (22.10.2026,
    # 18:56 UTC - это был наш же плановый рестарт).
    _write_heartbeat(note="starting")
    task = asyncio.create_task(_poller_loop())
    yield
    task.cancel()


app = FastAPI(
    title="CleanTrade Payment API",
    description="Приём USDT (TRC20) по уникальным суммам + вебхук об оплате.",
    version=API_VERSION,
    lifespan=lifespan,
)
app.include_router(admin.router)


def _rate_rules(path: str):
    """Какие окна применяются к пути: [(ведро, лимит, окно_сек), ...].

    Лимиты разные намеренно: живая страница опрашивает /v1/tg/status раз в
    3 секунды (20 запросов в минуту - это нормальное поведение человека), а
    37 646 попыток подписки за сутки - нет.
    """
    if path.startswith("/v1/subscribe"):
        return [("sub_min", RATE_SUBSCRIBE_PER_MIN, 60.0),
                ("sub_hour", RATE_SUBSCRIBE_PER_HOUR, 3600.0)]
    if path.startswith("/v1/tg/"):
        return [("tg", RATE_TG_PER_MIN, 60.0)]
    if path.startswith("/admin"):
        return [("admin", RATE_ADMIN_PER_MIN, 60.0)]
    if path.startswith("/v1/"):
        return [("api", RATE_API_PER_MIN, 60.0)]
    return [("public", RATE_PUBLIC_PER_MIN, 60.0)]


@app.middleware("http")
async def _guard(request: Request, call_next):
    """Блок-лист и лимит частоты - ДО любой работы с данными.

    Ставится первым рубежом: заблокированный адрес не доходит ни до хранилища,
    ни до TronScan, ни до отправки писем в Telegram.
    """
    ip = _client_ip(request)
    path = request.url.path
    rule = guard.blocked(ip)
    if rule:
        events.log_event("blocked_ip", ip=ip, rule=rule, path=path)
        return JSONResponse(status_code=403, content={"detail": "forbidden"})
    for bucket, limit, window in _rate_rules(path):
        ok, used = guard.allow(ip, bucket, limit, window)
        if not ok:
            events.log_event("rate_limited", ip=ip, path=path, bucket=bucket,
                             limit=limit, used=used)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(int(window))},
                content={"detail": "too many requests - please slow down"},
            )
    return await call_next(request)


@app.get("/healthz")
def health():
    return {"ok": True, "mock": config.USE_MOCK, "version": API_VERSION,
            "support_bot": tgbot.enabled(), "tg_auth": bool(SUPPORT_SECRET)}


@app.post("/v1/orders", status_code=201)
def create_order(req: OrderRequest, request: Request,
                 x_api_key: Optional[str] = Header(default=None)):
    """Создать заказ: кошелёк получателя обязателен, сумма уникальна В ЕГО СЕТИ.

    Всё, что пришло от клиента, проверяем ДО записи: адрес - base58check,
    сумма - Decimal до копеек, текстовые поля - без управляющих символов,
    вебхук - через гард SSRF. Заказ с мусором не создаётся и не тратит
    запросы к TronScan.
    """
    ip = _client_ip(request)
    role, sub = _resolve_key(x_api_key, ip)
    # Чей заказ: по id записи берётся ПЕРСОНАЛЬНЫЙ секрет подписи вебхука.
    owner_id = ((sub or {}).get("id") or "") if role == "sub" else ""
    webhook_url = _check_webhook(req.webhook_url)

    try:
        wallet = validate_trc20(req.wallet)
    except WalletAddressError as exc:
        events.log_event("wallet_rejected", wallet=(req.wallet or "")[:80],
                         error=str(exc), ip=ip)
        raise HTTPException(status_code=422, detail="invalid wallet: {}".format(exc))

    # Наш кошелёк - только для оплаты подписки (внутри /v1/subscribe). Клиенту
    # он доступен лишь мастер-ключом: иначе чужой заказ уедет на наш счёт.
    if role == "sub" and config.TRX_WALLET and wallet == config.TRX_WALLET:
        events.log_event("wallet_rejected", wallet=wallet,
                         error="service_wallet", ip=ip, key=x_api_key[:12])
        raise HTTPException(
            status_code=422,
            detail="this is the CleanTrade service wallet (subscription payments) - "
                   "pass the wallet of your own store",
        )

    if role == "sub":
        if not subscribers.check_limit(sub):
            events.log_event("limit_reached", key=x_api_key[:12], plan=sub["plan"],
                             limit=subscribers.PLANS[sub["plan"]]["limit"])
            raise HTTPException(
                status_code=402,
                detail="monthly order limit reached ({} on {} plan). "
                       "Upgrade: https://pay.cleantrade.info".format(
                           subscribers.PLANS[sub["plan"]]["limit"], sub["plan"]),
            )

    if req.amount is not None:
        # Явную сумму не подменяем: клиент назвал цену - получает её или 409.
        order = orders.create_order_if_free(
            orders.money(req.amount), req.product_id, webhook_url, wallet,
            req.customer_ref or "", owner_id=owner_id)
        if order is None:
            raise HTTPException(
                status_code=409,
                detail="amount {} is already in use by an active order on this "
                       "wallet - wait for it to expire or send another amount".format(
                           req.amount),
            )
    else:
        # Уникальную сумму выбираем среди свободных НА ЭТОМ кошельке; если пару
        # (кошелёк, сумма) заняли между выбором и записью - берём следующую.
        order = None
        for _ in range(5):
            candidate = orders.generate_unique_amount(
                config.BASE_AMOUNT, orders.used_amounts(wallet))
            if candidate is None:
                break
            order = orders.create_order_if_free(
                candidate, req.product_id, webhook_url, wallet,
                req.customer_ref or "", owner_id=owner_id)
            if order is not None:
                break
        if order is None:
            raise HTTPException(
                status_code=409,
                detail="no free unique amounts left on this wallet right now - "
                       "please retry in a minute",
            )

    # Чей это заказ - доказательство, а не догадка: до 22.10.2026 ключ в
    # заказе не сохранялся, и по базе нельзя было сказать, кто его создал.
    if x_api_key and not order.get("api_key"):
        orders.set_api_key(order["id"], x_api_key)

    if role == "sub":
        subscribers.increment_orders(x_api_key)
    return _order_payload(order)


@app.get("/v1/orders/{order_id}")
def get_order(order_id: str, request: Request,
              x_api_key: Optional[str] = Header(default=None)):
    """Статус заказа. Заказы тарифов (plan:...) доступны по order_id без ключа -
    это трек-код для страницы подписки; остальные - только с API-ключом."""
    order = orders.get_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    if x_api_key:
        _resolve_key(x_api_key, _client_ip(request))
    elif not (order.get("product_id") or "").startswith("plan:"):
        raise HTTPException(status_code=401, detail="missing api key")
    return order


@app.post("/v1/tg/start", status_code=201)
def tg_start(request: Request):
    """Шаг 1 из 2: выдать код, ссылку в Telegram и имя бота.

    Шаг 2 обязателен: открыть в ответе поле deep_link и нажать в боте Start.
    Только тогда код станет confirmed - бот поддержки (единственный слушатель
    апдейтов) сам сообщит нам об этом, и tg_id придёт от Telegram, а не от
    браузера. Никаких заголовков и секретов шагу 2 не нужно, нужен только
    Telegram с этой ссылкой.

    Пока Start не нажат: status=pending, а POST /v1/mykey отвечает
    401 tg_not_confirmed. Когда нажали - GET /v1/tg/status?code=... отдаёт
    status=confirmed и ваш api_key.
    """
    ip = _client_ip(request)
    rec = tgauth.create_code(ip)
    events.log_event("tg_start", ip=ip, code=rec["code"][:8])
    return {
        "code": rec["code"],
        "status": rec["status"],
        "deep_link": tgauth.deep_link(rec["code"]),
        "bot": tgauth.bot_username(),
        "expires_in": tgauth.ttl(),
        "expires_at": rec["expires_at"],
    }


@app.get("/v1/tg/status")
def tg_status(code: str = ""):
    """Статус кода: pending/confirmed/expired, а с ним tg_id и ключ, если он есть."""
    rec = tgauth.get(code)
    if not rec:
        raise HTTPException(status_code=404, detail="unknown or expired code")
    out = {
        "code": rec["code"],
        "status": rec["status"],
        "tg_id": rec.get("tg_id"),
        "username": rec.get("username") or "",
        "first_name": rec.get("first_name") or "",
        "expires_at": rec.get("expires_at"),
    }
    sub = subscribers.get_by_tg(rec.get("tg_id")) if rec.get("tg_id") else None
    if sub:
        plan = subscribers.PLANS.get(sub.get("plan"), {})
        out.update({
            "api_key": sub["key"],
            "plan": sub.get("plan"),
            "plan_name": plan.get("name"),
            "limit": plan.get("limit"),
            "plan_expires_at": sub.get("plan_expires_at"),
        })
    return out


@app.post("/v1/tg/confirm", include_in_schema=False)
def tg_confirm(req: TgConfirmRequest, request: Request,
               x_support_secret: Optional[str] = Header(default=None)):
    """Внутренний хук бота поддержки: человек нажал Start, код подтверждён.

    Доступен только по общему секрету (X-Support-Secret) - снаружи подтвердить
    чужой код нельзя, иначе привязка Telegram теряет смысл. Клиентам он не
    нужен и в публичной схеме (/docs, /openapi.json) не показывается: бот
    ходит к нему по петле, шаг 2 для клиента - нажать Start по deep_link.
    """
    _check_support_secret(x_support_secret, request)
    ip = _client_ip(request)
    rec = tgauth.confirm(req.code, req.tg_id, req.username or "",
                         req.first_name or "", ip)
    if not rec:
        events.log_event("tg_confirm_failed", code=req.code[:8], tg_id=req.tg_id,
                         ip=ip)
        raise HTTPException(status_code=410, detail="code expired or unknown")
    events.log_event("tg_confirmed", tg_id=req.tg_id,
                     username=req.username or "", ip=ip)
    sub = subscribers.get_by_tg(req.tg_id)
    sent = _notify_key(sub) if sub else False
    return {
        "ok": True,
        "tg_id": req.tg_id,
        "username": req.username or "",
        "has_key": bool(sub),
        "key_sent": sent,
        "plan": (sub or {}).get("plan") or "",
    }


@app.get("/v1/tg/mykey", include_in_schema=False)
def tg_mykey(request: Request, tg_id: int = 0,
             x_support_secret: Optional[str] = Header(default=None)):
    """Внутренний запрос бота: ключ по tg_id (команда /key). Только по секрету.

    В публичной схеме не показывается: клиент берёт ключ через POST /v1/mykey
    после того, как нажал Start в боте.
    """
    _check_support_secret(x_support_secret, request)
    sub = subscribers.get_by_tg(tg_id)
    if not sub:
        raise HTTPException(status_code=404, detail="no subscription for this telegram")
    plan = subscribers.PLANS.get(sub.get("plan"), {})
    return {
        "api_key": sub["key"],
        "webhook_secret": sub.get("webhook_secret") or "",
        "plan": sub.get("plan"),
        "plan_name": plan.get("name"),
        "limit": plan.get("limit"),
        "plan_expires_at": sub.get("plan_expires_at"),
        "orders_month": sub.get("orders_month", 0),
        "tg_username": sub.get("tg_username") or "",
    }


@app.post("/v1/mykey")
def my_key(req: MyKeyRequest):
    """«Мой ключ» с сайта: код подтверждён в Telegram - отдаём ключ и срок.

    Закрывает старую дыру «потерял ключ - потерял деньги»: раньше ключ был
    только в ответе на первый запрос и в вебхуке.
    """
    rec = tgauth.get(req.tg_code)
    if not rec or rec.get("status") not in (tgauth.STATUS_CONFIRMED,
                                            tgauth.STATUS_USED) \
            or not rec.get("tg_id"):
        raise CodedError(401,
                            "telegram not confirmed - open the link and press Start",
                            "tg_not_confirmed")
    sub = subscribers.get_by_tg(rec["tg_id"])
    if not sub:
        raise HTTPException(status_code=404, detail="no subscription for this telegram")
    plan = subscribers.PLANS.get(sub.get("plan"), {})
    events.log_event("mykey_lookup", tg_id=int(rec["tg_id"]))
    return {
        "api_key": sub["key"],
        "webhook_secret": sub.get("webhook_secret") or "",
        "plan": sub.get("plan"),
        "plan_name": plan.get("name"),
        "limit": plan.get("limit"),
        "status": sub.get("status"),
        "plan_expires_at": sub.get("plan_expires_at"),
        "orders_month": sub.get("orders_month", 0),
        "tg_username": sub.get("tg_username") or "",
    }


@app.post("/v1/subscribe", status_code=201)
def subscribe(req: SubscribeRequest, request: Request):
    """Публичная подписка на тариф: заказ plan:<тариф> на точную сумму.

    Личность - подтверждённый Telegram (tg_code) либо легаси customer_ref.
    Free (цена 0) активируется сразу - ключ уже в ответе, и уходит в бот.
    Платные: после оплаты поллер активирует план, ключ подписчика уходит
    в вебхук (webhook_url), в Telegram-бот и в GET /v1/orders/{id}.
    """
    plan = subscribers.get_plan(req.plan)
    if not plan:
        raise HTTPException(status_code=400,
                            detail="unknown plan: free/starter/pro/business/starter_yearly/pro_yearly/business_yearly")

    customer_ref, tg_id, tg_username = _identity(req)
    webhook_url = _check_webhook(req.webhook_url)
    ip = _client_ip(request)
    subscribers.touch_ip(customer_ref, ip)
    _subscribe_ip_guard(ip)

    if not plan["price"]:
        return _free_subscribe(req.plan, customer_ref, webhook_url, ip,
                               tg_id=tg_id, tg_username=tg_username)

    # Повторная заявка на уже активный тариф не должна брать деньги второй раз
    # (человек закрыл вкладку и вернулся) - отдаём тот же ключ. Продление -
    # только явным renew=true.
    active = subscribers.get_by_tg(tg_id) if tg_id else None
    if active is None:
        active = subscribers.get_by_customer_ref(customer_ref)
    if (active and active.get("status") == "active"
            and active.get("plan") == req.plan and _plan_alive(active)
            and not req.renew):
        events.log_event("subscribe_reused", customer_ref=customer_ref,
                         plan=req.plan, tg_id=tg_id, ip=ip)
        return _reused_payload(req.plan, active)

    amount = plan["price"]
    product_id = "plan:" + req.plan

    # Двойной клик по кнопке или перезагрузка страницы: своя же неоплаченная
    # заявка на этот тариф - отдаём её, а не создаём вторую на те же деньги.
    mine = orders.find_pending_plan(product_id, customer_ref)
    if mine:
        events.log_event("subscribe_pending_reused", plan=req.plan,
                         order_id=mine["id"], customer_ref=customer_ref, ip=ip)
        return _plan_payload(mine, req.plan, customer_ref=customer_ref,
                             tg_id=tg_id, tg_username=tg_username,
                             verified=bool(tg_id), reused=True)

    # Один адрес не должен держать очередь заявок: иначе абуз просто переезжает
    # с «держу сумму тарифа» на «держу сто заявок и не плачу».
    prefix = subscribers.subnet_key(ip)
    if MAX_PENDING_PER_IP > 0 and prefix:
        pending = orders.count_pending_by_prefix(prefix)
        if pending >= MAX_PENDING_PER_IP:
            events.log_event("pending_limit", ip=ip, prefix=prefix,
                             pending=pending, limit=MAX_PENDING_PER_IP)
            raise HTTPException(
                status_code=429,
                detail="too many unpaid orders from your network - "
                       "complete them or wait for them to expire",
            )

    # Проверка «сумма свободна» и создание заказа - ОДНОЙ операцией под замком.
    # По отдельности между ними было окно: два одновременных запроса на один
    # тариф оба видели 9.99 свободной, создавали два заказа на 9.99, и один
    # настоящий перевод мог быть засчитан обоим (двойная выдача).
    # Пул сумм на тариф: одна чужая неоплаченная заявка больше не держит тариф
    # целиком (17-18.09.2026 так держали пять тарифов сразу).
    order = orders.create_order_reserving(
        amount, product_id, webhook_url, config.TRX_WALLET, customer_ref,
        tg_id=tg_id, tg_username=tg_username, ip=ip, ip_prefix=prefix,
        candidates=orders.plan_amount_candidates(amount))
    if order is None:
        events.log_event("amount_busy", plan=req.plan, amount=amount, ip=ip,
                         customer_ref=customer_ref)
        raise HTTPException(
            status_code=409,
            detail="all amounts of this plan are reserved by pending orders "
                   "for up to {} minutes - please try again soon".format(
                       max(1, config.PLAN_ORDER_TTL // 60)),
        )

    payload = _plan_payload(order, req.plan, customer_ref=customer_ref,
                            tg_id=tg_id, tg_username=tg_username,
                            verified=bool(tg_id))
    subscribers.subscribe_ip_register(ip)
    return payload


@app.get("/v1/subscribers/me")
def my_subscription(request: Request,
                    x_api_key: Optional[str] = Header(default=None)):
    """Информация о подписке: план, лимит, использовано заказов в месяце."""
    role, sub = _resolve_key(x_api_key, _client_ip(request))
    if role == "master":
        return {"plan": "master", "plan_name": "Master", "limit": None,
                "orders_month": None, "status": "active", "expires_at": None}
    plan = subscribers.PLANS.get(sub.get("plan", "free"))
    return {
        "key": sub["key"],
        "webhook_secret": sub.get("webhook_secret") or "",
        "customer_ref": sub.get("customer_ref"),
        "plan": sub["plan"],
        "plan_name": plan["name"],
        "limit": plan["limit"],
        "orders_month": sub.get("orders_month", 0),
        "status": sub.get("status"),
        "expires_at": sub.get("plan_expires_at"),
    }



# Пульс поллера: файл, по которому сторож понимает, жив ли детект.
# Без него «тишина» неотличима от «нет заказов» - 22.10.2026 детект стоял
# 50 минут, и никто об этом не узнал.
POLL_HEARTBEAT = config.DATA_DIR / "poller_heartbeat.json"


def _write_heartbeat(ok: bool = True, error: str = "", note: str = "") -> None:
    """Пульс поллера: единственный признак, по которому сторож отличает
    «нет заказов» от «детект стоит». Не записался - говорим вслух."""
    try:
        POLL_HEARTBEAT.write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ts_epoch": time.time(), "ok": ok, "error": error, "note": note,
        }), encoding="utf-8")
    except Exception as exc:
        log.warning("Пульс поллера не записан: %s: %s", type(exc).__name__, exc)


async def _poll_once() -> None:
    """Обёртка: пульс после каждого цикла + ошибка цикла вслух."""
    ok, error = True, ""
    try:
        await _poll_once_inner()
    except Exception as exc:
        ok = False
        error = "{}: {}".format(type(exc).__name__, exc)[:300]
        log.exception("Поллер упал")
        events.log_event("poller_error", error=error)
    # Пульс не записался - сторож решит, что детект мёртв. Это лучше, чем
    # молчание, но причину надо видеть, а не прятать в pass.
    _write_heartbeat(ok=ok, error=error)


@app.exception_handler(CodedError)
async def _coded_error_handler(request: Request, exc: CodedError):
    """detail остаётся строкой (старые клиенты), code едет рядом."""
    return JSONResponse(status_code=exc.status_code,
                        content={"detail": exc.detail, "code": exc.code})
