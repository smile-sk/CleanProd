"""Telegram-подтверждение для подписки CleanTrade Payment API (v0.3.5).

Зачем: раньше подписка опознавала клиента по строке `customer_ref` (email или
@username), которую он вписывал руками. Строку можно написать любую, поэтому
«один клиент = один ключ» не держалось: пять адресов - пять бесплатных ключей,
а чужой @username - вообще чужой ключ. Теперь личность - tg_id (число), и его
подтверждает сам Telegram.

Поток:
  сайт   POST /v1/tg/start          -> {code, deep_link: t.me/<bot>?start=payauth_<code>}
  человек жмёт ссылку и Start       -> бот ловит /start payauth_<code>
  бот    POST /v1/tg/confirm        -> (общий секрет) отмечает код confirmed
  сайт   GET  /v1/tg/status?code=.. -> {status: confirmed, tg_id, username}
  сайт   POST /v1/subscribe {tg_code, plan} -> ключ подписчика привязан к tg_id

Почему tg_id, а не @username: username человек меняет за минуту и он не
уникален исторически; числовой id постоянен. Бот поддержки - единственный
слушатель апдейтов (getUpdates), платёжка только говорит (sendMessage), иначе
Telegram отвечает 409 Conflict и апдейты теряются между двумя процессами.

Хранилище - data/tg_auth.json, коды живут TG_AUTH_TTL секунд (по умолчанию 900).
"""
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

LOCK = threading.RLock()
AUTH_PATH = config.DATA_DIR / "tg_auth.json"

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_EXPIRED = "expired"
STATUS_USED = "used"        # код отработал (по нему выдали ключ / создали заказ)

MAX_RECORDS = 2000          # старые коды вычищаем: файл не должен расти вечно
DEFAULT_TTL = 900           # 15 минут человеку хватает с запасом


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def ttl() -> int:
    return config.get_int("TG_AUTH_TTL", DEFAULT_TTL)


def bot_username() -> str:
    return config.get("SUPPORT_BOT_USERNAME", "CleanTradeSupport_bot")


def deep_link(code: str) -> str:
    """Ссылка, по которой человек жмёт Start - это и есть подтверждение."""
    return "https://t.me/{}?start=payauth_{}".format(bot_username(), code)


def _load() -> dict:
    if not AUTH_PATH.exists():
        return {}
    try:
        data = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    try:
        AUTH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except OSError:
        pass


def _purge(data: dict) -> dict:
    """Выкинуть просроченные и лишние коды (свежие не трогаем)."""
    if len(data) <= MAX_RECORDS:
        keep = {k: v for k, v in data.items()
                if _parse(v.get("expires_at") or "") and
                _parse(v["expires_at"]) > _now() - timedelta(hours=1)}
        return keep if len(keep) != len(data) else data
    items = sorted(data.items(), key=lambda kv: kv[1].get("created_at") or "")
    return dict(items[-MAX_RECORDS:])


def create_code(ip: str = "") -> dict:
    """Новый код подтверждения. Возвращает запись целиком."""
    # 128 бит, а не 64: код ездит в ссылке t.me и в query статуса -
    # угадывать его подбором не должно быть возможным в принципе.
    code = secrets.token_hex(16)
    now = _now()
    rec = {
        "code": code,
        "status": STATUS_PENDING,
        "tg_id": None,
        "username": "",
        "first_name": "",
        "ip": ip or "",
        "created_at": _iso(now),
        "expires_at": _iso(now + timedelta(seconds=ttl())),
        "confirmed_at": None,
        "used_at": None,
        "uses": 0,
    }
    with LOCK:
        data = _purge(_load())
        data[code] = rec
        _save(data)
    return rec


def get(code: str) -> Optional[dict]:
    """Запись по коду. Просроченный код помечаем expired и отдаём как есть."""
    if not code:
        return None
    with LOCK:
        data = _load()
        rec = data.get(code)
        if not rec:
            return None
        exp = _parse(rec.get("expires_at") or "")
        if rec.get("status") == STATUS_PENDING and exp and exp < _now():
            rec["status"] = STATUS_EXPIRED
            data[code] = rec
            _save(data)
        return dict(rec)


def confirm(code: str, tg_id: int, username: str = "", first_name: str = "",
            ip: str = "") -> Optional[dict]:
    """Подтвердить код от имени Telegram-аккаунта. None - кода нет/просрочен."""
    if not code or not tg_id:
        return None
    with LOCK:
        data = _load()
        rec = data.get(code)
        if not rec:
            return None
        exp = _parse(rec.get("expires_at") or "")
        if rec.get("status") == STATUS_EXPIRED or (exp and exp < _now()):
            rec["status"] = STATUS_EXPIRED
            data[code] = rec
            _save(data)
            return None
        rec["status"] = STATUS_CONFIRMED
        rec["tg_id"] = int(tg_id)
        rec["username"] = username or ""
        rec["first_name"] = first_name or ""
        rec["confirmed_at"] = _iso(_now())
        if ip:
            rec["confirm_ip"] = ip
        data[code] = rec
        _save(data)
        return dict(rec)


def mark_used(code: str) -> Optional[dict]:
    """Код отработал: по нему выдали ключ (или создали заказ).

    Статус переводим в used - расход кода видно в записи и в ленте событий.
    Повторное использование used-кода не запрещаем: страница держит один и
    тот же код, пока человек на ней (кнопка «Получить ключ», «Мой ключ»), и
    жёсткий 401 ломал бы обычное нажатие второй раз. Вместо запрета в
    main.py такая попытка пишется событием tg_code_reused, а повторная
    заявка на тот же тариф отдаёт уже созданный заказ, а не новую оплату.
    """
    if not code:
        return None
    with LOCK:
        data = _load()
        rec = data.get(code)
        if not rec:
            return None
        rec["uses"] = int(rec.get("uses", 0)) + 1
        rec["used_at"] = _iso(_now())
        if rec.get("status") == STATUS_CONFIRMED:
            rec["status"] = STATUS_USED
        data[code] = rec
        _save(data)
        return dict(rec)


def code_of_tg(tg_id: int) -> Optional[str]:
    """Последний подтверждённый код этого Telegram - для повторных запросов."""
    if not tg_id:
        return None
    with LOCK:
        items = [v for v in _load().values()
                 if v.get("tg_id") == int(tg_id)
                 and v.get("status") in (STATUS_CONFIRMED, STATUS_USED)]
    if not items:
        return None
    items.sort(key=lambda v: v.get("confirmed_at") or "")
    return items[-1]["code"]
