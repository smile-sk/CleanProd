"""Хранилище заказов CleanTrade Payment API.

Состояние - data/orders_api.json (отдельно от заказов бота pay_bot,
чтобы два сервиса не дрались за файл).

Потокобезопасно: threading.Lock - FastAPI sync-эндпоинты крутятся в пуле потоков.
"""
import json
from typing import Optional, List, Set
import random
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import config
from payment_api import webhook, events, audit

LOCK = threading.RLock()
ORDERS_PATH = config.DATA_DIR / "orders_api.json"

STATUS_CREATED = "created"
STATUS_PAID = "paid"
STATUS_EXPIRED = "expired"

CENT = Decimal("0.01")


def money(value) -> float:
    """Сумма до копеек - ОДНА точка округления на весь сервис.

    Деньги не должны складываться в двоичной дроби: 8.99 + 0.57 даёт
    9.560000000000001, и перевод на 9.56 расходится с заказом. Здесь Decimal
    -> округление до копейки -> float только на запись в JSON.
    """
    try:
        return float(Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    if not ORDERS_PATH.exists():
        return {}
    try:
        return json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(orders: dict) -> None:
    ORDERS_PATH.write_text(json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_unique_amount(base: float, used: Set[float]) -> Optional[float]:
    """base + 0.50..0.99 - уникальная сумма заказа (base=9 -> 9.50..9.99).

    Каждая сумма принадлежит ровно одному активному заказу: детект по сумме,
    а не по адресу - можно принимать платежи на один кошелёк.
    """
    candidates = [round(base + i / 100, 2) for i in range(50, 100)]
    random.shuffle(candidates)
    for c in candidates:
        if c not in used:
            return c
    return None


def used_amounts(wallet: Optional[str] = None) -> Set[float]:
    """Суммы, занятые ЖИВЫМИ заказами.

    Уникальность считается ВНУТРИ одного кошелька: мы смотрим конкретную сумму
    на конкретном адресе, поэтому 9.76 на кошельке A и 9.76 на кошельке B -
    два разных платежа и никакой неоднозначности. wallet=None - все кошельки
    сразу (нужно только для общей картины).
    """
    with LOCK:
        return {
            o["amount"] for o in _load().values()
            if o["status"] == STATUS_CREATED
            and (wallet is None or (o.get("wallet") or "") == wallet)
        }


def is_amount_used(amount: float, wallet: Optional[str] = None) -> bool:
    """Занята ли сумма живым заказом НА ЭТОМ кошельке (тарифы и явные суммы).

    Не вызывает used_amounts() (он сам берёт LOCK) - чтобы не дедлокнуть
    нереентерабельный Lock; LOCK здесь RLock, но вложенный захват убираем явно.
    """
    with LOCK:
        return any(
            o.get("amount") == amount and o.get("status") == STATUS_CREATED
            and (wallet is None or (o.get("wallet") or "") == wallet)
            for o in _load().values()
        )


def _build_order(amount: float, product_id: str, webhook_url: str, wallet: str,
                 customer_ref: str = "", tg_id=None, tg_username: str = "",
                 ip: str = "", ip_prefix: str = "", owner_id: str = "") -> dict:
    order_id = uuid.uuid4().hex[:12]
    return {
        "id": order_id,
        "product_id": product_id,
        "amount": money(amount),
        "wallet": wallet,
        "status": STATUS_CREATED,
        "webhook_url": webhook_url or "",
        # Чей это заказ (id записи подписчика): по нему берётся ПЕРСОНАЛЬНЫЙ
        # секрет подписи вебхука. Пусто - заказ мастер-ключа или старый, тогда
        # подпись общим секретом сервиса.
        "owner_id": owner_id or "",
        "customer_ref": customer_ref or "",
        "tg_id": int(tg_id) if tg_id else None,
        "tg_username": tg_username or "",
        "ip": ip or "",
        "ip_prefix": ip_prefix or "",
        "created_at": _now(),
        "paid_at": None,
        "tx_id": None,
        "api_key": None,
        "webhook_attempts": 0,
        "webhook_delivered": False,
    }
def create_order(amount: float, product_id: str, webhook_url: str, wallet: str,
                 customer_ref: str = "", tg_id=None, tg_username: str = "",
                 ip: str = "", ip_prefix: str = "", owner_id: str = "") -> dict:
    order = _build_order(amount, product_id, webhook_url, wallet,
                         customer_ref, tg_id, tg_username, ip, ip_prefix, owner_id)
    with LOCK:
        orders = _load()
        orders[order["id"]] = order
        _save(orders)
    _log_created(order)
    return order


def create_order_reserving(amount: float, product_id: str, webhook_url: str,
                           wallet: str, customer_ref: str = "", tg_id=None,
                           tg_username: str = "", ip: str = "",
                           candidates: Optional[list] = None,
                           ip_prefix: str = "", owner_id: str = "") -> Optional[dict]:
    """Занять СВОБОДНУЮ сумму и создать заказ - ОДНОЙ операцией под замком.

    Проверка is_amount_used() и create_order() по отдельности оставляли окно
    между собой: два одновременных запроса на один тариф оба видели сумму
    свободной, создавали два заказа на 9.99, и один перевод 9.99 USDT мог
    быть засчитан обоим. Здесь выбор суммы и запись неразделимы: кто первый,
    у того заказ, второй берёт следующую свободную сумму, а если свободных
    нет - получает None (и честный 409 наружу).

    candidates - пул сумм (для тарифа plan_amount_candidates): без него
    работаем по одной сумме, как раньше.
    """
    pool = [float(c) for c in (candidates or [amount])]
    with LOCK:
        orders = _load()
        used = {o.get("amount") for o in orders.values()
                if o.get("status") == STATUS_CREATED
                and (o.get("wallet") or "") == wallet}
        chosen = None
        for candidate in pool:
            if candidate not in used:
                chosen = candidate
                break
        if chosen is None:
            return None
        order = _build_order(chosen, product_id, webhook_url, wallet,
                             customer_ref, tg_id, tg_username, ip, ip_prefix,
                             owner_id)
        orders[order["id"]] = order
        _save(orders)
    _log_created(order)
    return order


def create_order_if_free(amount: float, product_id: str, webhook_url: str,
                         wallet: str, customer_ref: str = "", tg_id=None,
                         tg_username: str = "", ip: str = "",
                         ip_prefix: str = "", owner_id: str = "") -> Optional[dict]:
    """Создать заказ на ТОЧНО эту сумму, если она свободна НА ЭТОМ кошельке.

    Проверка и запись - одна операция под замком: между «сумма свободна» и
    «заказ создан» не остаётся окна, в котором второй запрос получит ту же
    пару (кошелёк, сумма). None - пара занята (наружу 409).
    """
    return create_order_reserving(
        amount, product_id, webhook_url, wallet, customer_ref,
        tg_id=tg_id, tg_username=tg_username, ip=ip, ip_prefix=ip_prefix,
        candidates=[amount], owner_id=owner_id,
    )


def find_pending_plan(product_id: str, customer_ref: str) -> Optional[dict]:
    """Уже созданный и ещё не оплаченный заказ на этот тариф у этого клиента.

    Двойной клик по кнопке, обновление страницы или повторное нажатие после
    подтверждения кода не должны плодить вторую заявку на ту же оплату.
    """
    if not product_id or not customer_ref:
        return None
    with LOCK:
        for o in _load().values():
            if (o.get("status") == STATUS_CREATED
                    and o.get("product_id") == product_id
                    and o.get("customer_ref") == customer_ref):
                return dict(o)
    return None


def plan_amount_candidates(price: float, span: int = 50) -> List[float]:
    """Суммы-кандидаты для тарифа: price, price-0.01 ... (span штук, все > 0).

    Одна фиксированная сумма на тариф означала, что ЛЮБАЯ чужая неоплаченная
    заявка держала тариф целиком: 17-18.09.2026 атакующий держал так пять
    тарифов сразу, и купить их было нельзя (36 821 отказ «сумма занята» на
    /v1/subscribe). Пул сумм делает эту тактику бессмысленной - каждый
    покупатель получает свою сумму. Покупатель платит не больше витринной
    цены: суммы только уменьшаются.
    """
    out: List[float] = []
    for i in range(max(1, int(span))):
        candidate = round(float(price) - i / 100.0, 2)
        if candidate > 0:
            out.append(candidate)
    return out


def count_pending_by_prefix(prefix: str) -> int:
    """Сколько неоплаченных тарифных заявок держит эта сеть.

    Один адрес не может занять очередь заказов целиком: иначе абуз просто
    переезжает с «держу сумму» на «держу сто заявок».
    """
    if not prefix:
        return 0
    with LOCK:
        return sum(
            1 for o in _load().values()
            if o.get("status") == STATUS_CREATED
            and o.get("ip_prefix") == prefix
        )


def _log_created(order: dict) -> None:
    events.log_event("order_created", order_id=order["id"], amount=order.get("amount"),
                     product_id=order.get("product_id"),
                     customer_ref=order.get("customer_ref") or "",
                     wallet=order.get("wallet") or "",
                     tg_id=order.get("tg_id"), ip=order.get("ip") or "")
    audit.log(order["id"], "created", wallet=order.get("wallet") or "",
              amount=order.get("amount"), product_id=order.get("product_id"),
              customer_ref=order.get("customer_ref") or "",
              webhook_url=order.get("webhook_url") or "",
              ip=order.get("ip") or "")


def get_order(order_id: str) -> Optional[dict]:
    with LOCK:
        return _load().get(order_id)


def list_waiting() -> List[dict]:
    with LOCK:
        return [o for o in _load().values() if o["status"] == STATUS_CREATED]


def used_tx_ids() -> Set[str]:
    """tx_id, уже привязанные к заказам - один перевод не зачитываем дважды."""
    with LOCK:
        return {o.get("tx_id") for o in _load().values() if o.get("tx_id")}


def list_grace(grace_seconds: int) -> List[dict]:
    """Истёкшие тарифные заказы, которые ещё стоит проверить на позднюю оплату.

    Тарифный заказ держит уникальную сумму около часа. Если человек перевёл
    деньги уже после истечения срока - перевод всё равно наш (кошелёк один),
    поэтому заказ можно оживить, а не терять покупателя и его деньги.
    """
    if grace_seconds <= 0:
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - grace_seconds
    out = []
    with LOCK:
        for o in _load().values():
            if o.get("status") != STATUS_EXPIRED:
                continue
            if not (o.get("product_id") or "").startswith("plan:"):
                continue
            if float(o.get("amount") or 0) <= 0:
                continue
            try:
                created = datetime.fromisoformat(o["created_at"]).timestamp()
            except Exception:
                continue
            if created >= cutoff:
                out.append(o)
    out.sort(key=lambda o: o.get("created_at") or "")
    return out


def mark_paid_late(order_id: str, tx_id: str) -> Optional[dict]:
    """Оживить истёкший тарифный заказ: деньги пришли позже срока."""
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order and order["status"] == STATUS_EXPIRED:
            order["status"] = STATUS_PAID
            order["paid_at"] = _now()
            order["tx_id"] = tx_id
            order["late"] = True
            _save(orders)
            events.log_event("order_paid_late", order_id=order_id,
                             amount=order["amount"], tx_id=tx_id)
            audit.log(order_id, "paid_late", tx_id=tx_id, amount=order["amount"],
                      wallet=order.get("wallet") or "", paid_at=order.get("paid_at"))
            return order
    return None


def list_pending_webhook() -> List[dict]:
    """Оплаченные заказы, которым ещё не доставлен вебхук (или нужен повтор)."""
    with LOCK:
        return [
            o for o in _load().values()
            if o["status"] == STATUS_PAID
            and o.get("webhook_url")
            and not o.get("webhook_delivered")
            and o.get("webhook_attempts", 0) < webhook.MAX_ATTEMPTS
        ]


def mark_paid(order_id: str, tx_id: str) -> Optional[dict]:
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order and order["status"] == STATUS_CREATED:
            order["status"] = STATUS_PAID
            order["paid_at"] = _now()
            order["tx_id"] = tx_id
            _save(orders)
            events.log_event("order_paid", order_id=order_id, amount=order["amount"], tx_id=tx_id)
            audit.log(order_id, "paid", tx_id=tx_id, amount=order["amount"],
                      wallet=order.get("wallet") or "", paid_at=order.get("paid_at"))
            return order
    return None


def set_owner_id(order_id: str, owner_id: str) -> None:
    """Привязать заказ к мерчанту: по owner_id берётся его секрет подписи.

    Для тарифных заказов и free владелец известен только после активации
    (запись подписчика создаётся в этот момент), поэтому ставим отдельным
    шагом, а не при создании заказа.
    """
    if not order_id or not owner_id:
        return
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order is not None and order.get("owner_id") != owner_id:
            order["owner_id"] = owner_id
            _save(orders)


def set_api_key(order_id: str, api_key: str) -> None:
    """Привязать ключ подписчика к оплаченному заказу тарифа."""
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order:
            order["api_key"] = api_key
            _save(orders)


def mark_expired(order_id: str, reason: str = "") -> None:
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order and order["status"] == STATUS_CREATED:
            order["status"] = STATUS_EXPIRED
            _save(orders)
            events.log_event("order_expired", order_id=order_id,
                             reason=reason or "timeout")
            audit.log(order_id, "expired", reason=reason or "timeout",
                      amount=order.get("amount"), wallet=order.get("wallet") or "",
                      created_at=order.get("created_at") or "")


def bump_webhook_attempts(order_id: str) -> None:
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order:
            order["webhook_attempts"] = order.get("webhook_attempts", 0) + 1
            _save(orders)


def mark_webhook_delivered(order_id: str) -> None:
    with LOCK:
        orders = _load()
        order = orders.get(order_id)
        if order:
            order["webhook_delivered"] = True
            _save(orders)


def list_all():
    """Все заказы, новые сверху (для админки)."""
    with LOCK:
        items = list(_load().values())
    items.sort(key=lambda o: o.get("created_at") or "", reverse=True)
    return items
