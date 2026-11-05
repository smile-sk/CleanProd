"""Подписчики CleanTrade Payment API (биллинг 3.2).

Тарифы (месячные):
  free       5     заказов/мес   $0
  starter    100   заказов/мес   $9.99
  pro        3000  заказов/мес   $29.99
  business   безлимит            $79.99
Годовые (цена x10 за 12 месяцев - два месяца в подарок):
  starter_yearly   100   заказов/мес   $99.90/год
  pro_yearly       3000  заказов/мес   $299.90/год
  business_yearly  безлимит            $799.90/год

Хранилище - data/subscribers.json. Ключ подписчика (sp_live_...) - это его
API-ключ мерчанта: каждый запрос к API проходит проверку лимита (402 при
исчерпании).

Оплата тарифа - через наш же API (собака ест свой корм):
  POST /v1/subscribe -> заказ plan:<тариф> -> оплата USDT ->
  поллер активирует план -> вебхук с api_key подписчика.

ИСКЛЮЧЕНИЕ - Free (цена 0): платить нечего, поэтому ключ выдаётся сразу же
в ответе на POST /v1/subscribe, минуя поллер (раньше заказ «на 0 USDT» уходил
в ожидание перевода нуля и просто истекал - ни один free-ключ не выдавался).
Так как free не стоит ничего, он ограничен по СЕТИ адреса: data/free_ips.json,
по умолчанию FREE_IP_DAILY_LIMIT активаций в сутки на подсеть (IPv6 -> /64,
IPv4 -> /24, 0 = без лимита). Счёт по одному точному адресу обходился ротацией
внутри сети: 17-18.09.2026 так выдали 213 бесплатных ключей с одного /64.

С v0.3.5 личность клиента - Telegram: подписчик хранит tg_id (число из подписи
Telegram, см. payment_api/tgauth.py), а customer_ref вида "tg:<id>" остаётся
человекочитаемой ссылкой. По tg_id держится правило «один Telegram - один
бесплатный ключ навсегда»: пять адресов больше не дают пять ключей, потому что
адрес вообще перестал быть личностью. IP пишем и копим историю (ips), но
использование ключа по IP не блокируем: у мобильных операторов один адрес на
тысячи абонентов, и жёсткий «один IP = один клиент» ударил бы по живым людям.
"""
import ipaddress
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import config
from payment_api import events

LOCK = threading.RLock()
SUBSCRIBERS_PATH = config.DATA_DIR / "subscribers.json"
FREE_IP_PATH = config.DATA_DIR / "free_ips.json"
# Счётчик СОЗДАННЫХ заявок на публичной двери (/v1/subscribe) - отдельно от
# free_ips.json: тот считает выданные бесплатные ключи, а тут все заявки,
# включая платные (у них свой вебхук, и free-лимит их не видел).
SUBSCRIBE_IP_PATH = config.DATA_DIR / "subscribe_ips.json"

PLANS = {
    # Free не истекает: срок None - «один Telegram, один бесплатный ключ».
    "free":            {"name": "Free",            "price": 0.0,   "limit": 5,    "days": None},
    "starter":         {"name": "Starter",         "price": 9.99,  "limit": 100,  "days": 30},
    "pro":             {"name": "Pro",             "price": 29.99, "limit": 3000, "days": 30},
    "business":        {"name": "Business",        "price": 79.99, "limit": None, "days": 30},  # безлимит
    "starter_yearly":  {"name": "Starter Yearly",  "price": 99.90,  "limit": 100,  "days": 365},
    "pro_yearly":      {"name": "Pro Yearly",      "price": 299.90, "limit": 3000, "days": 365},
    "business_yearly": {"name": "Business Yearly", "price": 799.90, "limit": None, "days": 365},  # безлимит
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load() -> dict:
    if not SUBSCRIBERS_PATH.exists():
        return {}
    try:
        return json.loads(SUBSCRIBERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(subs: dict) -> None:
    SUBSCRIBERS_PATH.write_text(
        json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_plan(plan_id: str) -> Optional[dict]:
    return PLANS.get(plan_id)


def generate_key() -> str:
    return "sp_live_" + secrets.token_hex(16)


def new_webhook_secret() -> str:
    """Секрет подписи вебхука ЭТОГО мерчанта (48 hex, как общий WEBHOOK_SECRET).

    До 22.10.2026 подпись была одна на сервис: мерчант, знавший секрет, мог
    подделать вебхук другому. Теперь у каждой записи свой секрет, и подпись
    считается только им.
    """
    return secrets.token_hex(24)


def get_by_id(sub_id: str) -> Optional[dict]:
    """Подписчик по внутреннему id записи (им помечен заказ при создании)."""
    if not sub_id:
        return None
    with LOCK:
        for sub in _load().values():
            if sub.get("id") == sub_id:
                return sub
    return None


def secret_of_owner(owner_id: str) -> str:
    """Секрет подписи мерчанта по id записи из заказа ('' - секрета нет)."""
    sub = get_by_id(owner_id)
    return (sub or {}).get("webhook_secret") or ""


def rotate_webhook_secret(customer_ref: str = "", key: str = "",
                          tg_id=None) -> Optional[dict]:
    """Выдать новый секрет подписи: старый сразу перестаёт подходить.

    Нужно, когда секрет засветился (переписка, скриншот, чужой доступ к серверу
    мерчанта). Ключ API при этом не меняется - перевыпуск ключа отдельная
    операция и рвёт интеграцию целиком.
    """
    if not (customer_ref or key or tg_id):
        return None
    want_tg = None
    if tg_id:
        try:
            want_tg = int(tg_id)
        except (TypeError, ValueError):
            return None
    with LOCK:
        subs = _load()
        for sub in subs.values():
            hit = ((customer_ref and sub.get("customer_ref") == customer_ref)
                   or (key and sub.get("key") == key)
                   or (want_tg is not None and sub.get("tg_id")
                       and int(sub["tg_id"]) == want_tg))
            if not hit:
                continue
            sub["webhook_secret"] = new_webhook_secret()
            sub["webhook_secret_rotated_at"] = _now()
            _save(subs)
            events.log_event("webhook_secret_rotated",
                             customer_ref=sub.get("customer_ref") or "",
                             key=(sub.get("key") or "")[:12],
                             tg_id=sub.get("tg_id"))
            return sub
    return None


def backfill_webhook_secrets() -> int:
    """Выдать секрет записям, созданным до 22.10.2026. Возвращает число новых.

    Вызывается один раз при старте: без этого старые подписчики подписывались
    бы общим секретом сервиса, то есть дыра «один секрет на всех» осталась бы.
    """
    with LOCK:
        subs = _load()
        changed = 0
        for sub in subs.values():
            if not sub.get("webhook_secret"):
                sub["webhook_secret"] = new_webhook_secret()
                changed += 1
        if changed:
            _save(subs)
            events.log_event("webhook_secrets_backfill", count=changed)
        return changed


def get_by_key(api_key: str) -> Optional[dict]:
    with LOCK:
        for sub in _load().values():
            if sub.get("key") == api_key:
                return sub
    return None


def get_by_customer_ref(ref: str) -> Optional[dict]:
    with LOCK:
        for sub in _load().values():
            if sub.get("customer_ref") == ref:
                return sub
    return None


def get_by_tg(tg_id) -> Optional[dict]:
    """Подписчик по числовому Telegram id - настоящая личность клиента."""
    if not tg_id:
        return None
    try:
        tg_id = int(tg_id)
    except (TypeError, ValueError):
        return None
    with LOCK:
        for sub in _load().values():
            if sub.get("tg_id") and int(sub["tg_id"]) == tg_id:
                return sub
    return None


def touch_ip(customer_ref: str, ip: str) -> None:
    """Записать адрес клиента в историю (для разбора, не для блокировки)."""
    if not customer_ref or not ip:
        return
    with LOCK:
        subs = _load()
        for sub in subs.values():
            if sub.get("customer_ref") == customer_ref:
                ips = sub.setdefault("ips", [])
                if ip not in [x.get("ip") for x in ips if isinstance(x, dict)]:
                    ips.append({"ip": ip, "at": _now()})
                    ips[:] = ips[-20:]
                sub.setdefault("last_ip", ip)
                sub["last_seen_at"] = _now()
                _save(subs)
                return


def tg_of_ip(ip: str) -> list:
    """Какие Telegram-аккаунты приходили с этого адреса (для метки в админке)."""
    if not ip:
        return []
    out = []
    with LOCK:
        for sub in _load().values():
            if not sub.get("tg_id"):
                continue
            ips = [x.get("ip") for x in (sub.get("ips") or []) if isinstance(x, dict)]
            if ip in ips or sub.get("last_ip") == ip:
                out.append(sub["tg_id"])
    return out


def _reset_month_if_needed(sub: dict) -> bool:
    """Новый месяц - счётчик заказов с нуля. True, если сброс был."""
    if sub.get("month_key") != _month_key():
        sub["month_key"] = _month_key()
        sub["orders_month"] = 0
        return True
    return False


def check_limit(sub: dict) -> bool:
    """True - лимит не исчерпан (заказ можно создавать).

    Сброс месячного счётчика сохраняем на диск сразу: раньше он жил только
    в памяти и «доезжал» до файла случайно, следующим заказом.
    """
    plan = PLANS.get(sub.get("plan", "free"))
    if not plan or plan["limit"] is None:
        return True
    with LOCK:
        if _reset_month_if_needed(sub):
            subs = _load()
            for s in subs.values():
                if s.get("key") == sub.get("key"):
                    s["month_key"] = sub["month_key"]
                    s["orders_month"] = 0
                    _save(subs)
                    break
        return sub.get("orders_month", 0) < plan["limit"]


def increment_orders(api_key: str) -> None:
    """Учесть созданный заказ в счётчике месяца подписчика."""
    with LOCK:
        subs = _load()
        for sub in subs.values():
            if sub.get("key") == api_key:
                _reset_month_if_needed(sub)
                sub["orders_month"] = sub.get("orders_month", 0) + 1
                _save(subs)
                return


def activate_plan(customer_ref: str, plan_id: str, tg_id=None,
                  tg_username: str = "", ip: str = "") -> dict:
    """Активация/апгрейд тарифа после оплаты заказа plan:<plan>.

    Ключ подписчика сохраняется при апгрейде - клиент не теряет доступ.
    """
    now = datetime.now(timezone.utc)
    plan = PLANS.get(plan_id, {})
    days = plan.get("days")
    # days=None - тариф без срока (free): даты нет, ключ не умирает.
    expires = ((now + timedelta(days=days)).isoformat(timespec="seconds")
               if days else None)
    with LOCK:
        subs = _load()
        sub = None
        for s in subs.values():
            if s.get("customer_ref") == customer_ref:
                sub = s
                break
        if sub is None:
            sub = {
                "id": uuid.uuid4().hex[:12],
                "key": generate_key(),
                "webhook_secret": new_webhook_secret(),
                "customer_ref": customer_ref,
                "plan": plan_id,
                "status": "active",
                "created_at": _now(),
                "plan_started_at": _now(),
                "plan_expires_at": expires,
                "month_key": _month_key(),
                "orders_month": 0,
                "tg_id": int(tg_id) if tg_id else None,
                "tg_username": tg_username or "",
                "verified": bool(tg_id),
                "ips": ([{"ip": ip, "at": _now()}] if ip else []),
                "last_ip": ip or "",
            }
            subs[sub["id"]] = sub
        else:
            sub["plan"] = plan_id
            sub["status"] = "active"
            sub["plan_started_at"] = _now()
            sub["plan_expires_at"] = expires
            if not sub.get("webhook_secret"):
                # Запись из времён общего секрета - выдаём свой.
                sub["webhook_secret"] = new_webhook_secret()
            if tg_id:
                sub["tg_id"] = int(tg_id)
                sub["verified"] = True
            if tg_username:
                sub["tg_username"] = tg_username
            if ip:
                ips = sub.setdefault("ips", [])
                if ip not in [x.get("ip") for x in ips if isinstance(x, dict)]:
                    ips.append({"ip": ip, "at": _now()})
                    ips[:] = ips[-20:]
                sub["last_ip"] = ip
        _save(subs)
        events.log_event("plan_activated", customer_ref=customer_ref, plan=plan_id,
                         key=sub["key"], tg_id=sub.get("tg_id"), ip=ip or "")
        return sub


def is_expired(sub: Optional[dict]) -> bool:
    """Срок тарифа вышел. Нет даты - не истекает (free, легаси-записи)."""
    exp = (sub or {}).get("plan_expires_at") or ""
    if not exp:
        return False
    try:
        dt = datetime.fromisoformat(str(exp))
    except Exception:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= datetime.now(timezone.utc)


def mark_expired(api_key: str) -> Optional[dict]:
    """Пометить подписку истёкшей - ленивая сверка срока при запросе ключа.

    Отдельного суточного джоба нет: срок проверяется в тот момент, когда
    ключ реально пришёл с запросом, и статус тут же ложится на диск.
    """
    if not api_key:
        return None
    with LOCK:
        subs = _load()
        for sub in subs.values():
            if sub.get("key") == api_key and sub.get("status") == "active" \
                    and is_expired(sub):
                sub["status"] = "expired"
                sub["expired_at"] = _now()
                _save(subs)
                events.log_event("plan_expired",
                                 customer_ref=sub.get("customer_ref") or "",
                                 plan=sub.get("plan"), key=api_key[:12])
                return sub
    return None


# --- Анти-накрутка Free: лимит активаций на IP в сутки ---

def _free_load() -> dict:
    if not FREE_IP_PATH.exists():
        return {}
    try:
        return json.loads(FREE_IP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _free_save(data: dict) -> None:
    try:
        FREE_IP_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _sub_ip_load() -> dict:
    if not SUBSCRIBE_IP_PATH.exists():
        return {}
    try:
        return json.loads(SUBSCRIBE_IP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _sub_ip_save(data: dict) -> None:
    try:
        SUBSCRIBE_IP_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def subscribe_ip_allow(ip: str, limit: int) -> Tuple[bool, int]:
    '''(можно ли создать ещё одну заявку с этой СЕТИ, сколько создано сегодня).'''
    key = subnet_key(ip)
    if not key or limit <= 0:
        return True, 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with LOCK:
        rec = _sub_ip_load().get(key) or {}
        used = rec.get("count", 0) if rec.get("date") == today else 0
        return used < limit, used


def subscribe_ip_register(ip: str) -> None:
    '''Отметить созданную заявку (счётчик на сутки, старые дни вычищаются).'''
    key = subnet_key(ip)
    if not key:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with LOCK:
        data = {k: v for k, v in _sub_ip_load().items()
                if isinstance(v, dict) and v.get("date") == today}
        rec = data.get(key) or {"date": today, "count": 0}
        rec["count"] = rec.get("count", 0) + 1
        data[key] = rec
        _sub_ip_save(data)


def subnet_key(ip: str) -> str:
    """Сеть адреса: IPv6 -> /64, IPv4 -> /24 ('' если адрес не разобран).

    Лимит на ТОЧНЫЙ адрес обходится ротацией внутри одной сети - у атакующего
    17-18.09.2026 был /64, и 213 из 220 бесплатных ключей пришли из одной сети.
    Поэтому и лимит, и блокировка считаются сетью, а не адресом.
    """
    addr = _parse_addr(ip)
    if addr is None:
        return ""
    prefix = 64 if addr.version == 6 else 24
    try:
        return str(ipaddress.ip_network("{}/{}".format(addr, prefix), strict=False))
    except ValueError:
        return str(ip)[:64]


def _parse_addr(value: str):
    try:
        return ipaddress.ip_address(str(value or "").strip().strip("[]"))
    except ValueError:
        return None


def free_ip_allow(ip: str, limit: int) -> Tuple[bool, int]:
    """(можно ли выдать ещё один free-ключ с этой СЕТИ, сколько выдано сегодня)."""
    key = subnet_key(ip)
    if not key or limit <= 0:
        return True, 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with LOCK:
        rec = _free_load().get(key) or {}
        used = rec.get("count", 0) if rec.get("date") == today else 0
        return used < limit, used


def free_ip_register(ip: str, tg_id=None) -> None:
    """Отметить выданный free-ключ (счётчик на сутки, старые дни вычищаются).

    Кроме счётчика держим список tg_id, приходивших с адреса: несколько разных
    Telegram с одного IP - не блокировка, а метка для разбора человеком.
    """
    key = subnet_key(ip)
    if not key:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with LOCK:
        data = {k: v for k, v in _free_load().items()
                if isinstance(v, dict) and v.get("date") == today}
        rec = data.get(key) or {"date": today, "count": 0}
        rec["count"] = rec.get("count", 0) + 1
        if tg_id:
            tgs = rec.setdefault("tg_ids", [])
            if int(tg_id) not in tgs:
                tgs.append(int(tg_id))
        seen = rec.setdefault("ips", [])
        if ip not in seen:
            seen.append(ip)
            rec["ips"] = seen[-20:]
        data[key] = rec
        _free_save(data)


def free_ip_tg_count(ip: str) -> int:
    """Сколько РАЗНЫХ Telegram получало free-ключ с этого адреса сегодня."""
    key = subnet_key(ip)
    if not key:
        return 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with LOCK:
        rec = _free_load().get(key) or {}
    if rec.get("date") != today:
        return 0
    return len(rec.get("tg_ids") or [])


def list_all():
    """Все подписчики (для админки)."""
    with LOCK:
        return sorted(_load().values(), key=lambda s: s.get("created_at") or "")


def delete(customer_ref: str = "", key: str = "", tg_id=None) -> Optional[dict]:
    """Удалить подписчика по ссылке, ключу или tg_id (операция админки).

    Нужна для мусора: тестовые записи агента, дубли, накрутка. Ключ удалённого
    сразу перестаёт работать, а tg_id освобождается - иначе «один Telegram -
    один бесплатный ключ» держал бы место за удалённой записью.
    Заказы не трогаем: журнал платежей остаётся как есть.
    """
    if not (customer_ref or key or tg_id):
        return None
    want_tg = None
    if tg_id:
        try:
            want_tg = int(tg_id)
        except (TypeError, ValueError):
            return None
    with LOCK:
        subs = _load()
        target = None
        for sid, s in subs.items():
            if customer_ref and s.get("customer_ref") == customer_ref:
                target = sid
                break
            if key and s.get("key") == key:
                target = sid
                break
            if want_tg is not None and s.get("tg_id") and int(s["tg_id"]) == want_tg:
                target = sid
                break
        if target is None:
            return None
        gone = subs.pop(target)
        _save(subs)
    events.log_event("subscriber_deleted",
                     customer_ref=gone.get("customer_ref") or "",
                     key=gone.get("key") or "", tg_id=gone.get("tg_id"),
                     plan=gone.get("plan") or "")
    return gone
