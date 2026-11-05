"""
CleanProd - общая конфигурация.

Читает .env (секреты и настройки), резолвит все пути относительно корня
проекта, чтобы CleanProd одинаково запускался и на VPS, и локально.
Секреты НЕ хранятся в коде - только в .env (gitignored).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env(path: Path) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_ENV = _load_env(BASE_DIR / ".env")
# Значения из .env попадают в os.environ, но НЕ перетирают уже заданные
os.environ.update({k: v for k, v in _ENV.items() if k not in os.environ})


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, _ENV.get(key, default))


def get_int(key: str, default: int) -> int:
    try:
        return int(get(key, str(default)))
    except ValueError:
        return default


def get_float(key: str, default: float) -> float:
    try:
        return float(get(key, str(default)))
    except ValueError:
        return default


# --- Пути: всё состояние в data/ (gitignored) ---
DATA_DIR = Path(get("DATA_DIR", str(BASE_DIR / "data")))
LOG_DIR = Path(get("LOG_DIR", str(DATA_DIR / "logs")))

ORDERS_FILE = DATA_DIR / "orders.json"
TOKENS_FILE = DATA_DIR / "tokens.txt"
USED_TOKENS_FILE = DATA_DIR / "used_tokens.txt"
AUTHORIZED_USERS_FILE = DATA_DIR / "authorized_users.txt"
BLOCKED_USERS_FILE = DATA_DIR / "blocked_users.txt"
STAT_FILE = LOG_DIR / "stat.log"

ARCHIVE_PATH = Path(get("ARCHIVE_PATH", str(BASE_DIR / "assets" / "CleanTrail.zip")))
MEDIA_DIR = Path(get("MEDIA_DIR", str(BASE_DIR / "assets" / "media")))


# --- Telegram ---
SALES_BOT_TOKEN = get("SALES_BOT_TOKEN")            # @CleanTrade_bot
# Бот поддержки @CleanTradeSupport_bot (проект support-engine) - ЕДИНСТВЕННЫЙ
# слушатель апдейтов (getUpdates). Платёжка только отправляет (sendMessage):
# два читателя одного токена Telegram разводит ошибкой 409 Conflict.
SUPPORT_BOT_TOKEN = get("SUPPORT_BOT_TOKEN")
SUPPORT_BOT_USERNAME = get("SUPPORT_BOT_USERNAME", "CleanTradeSupport_bot")
# Общий секрет платёжки и бота: бот подтверждает им код подписки
# (POST /v1/tg/confirm) и спрашивает ключ клиента (POST /v1/tg/mykey).
SUPPORT_SHARED_SECRET = get("SUPPORT_SHARED_SECRET")
TG_AUTH_TTL = get_int("TG_AUTH_TTL", 900)          # срок жизни кода подтверждения
FREE_TG_IP_FLAG = get_int("FREE_TG_IP_FLAG", 3)    # >N разных TG с одного IP = метка
ACCESS_BOT_TOKEN = get("ACCESS_BOT_TOKEN")          # @HexusCrypto_bot
ACCESS_BOT_USERNAME = get("ACCESS_BOT_USERNAME", "HexusCrypto_bot")
ADMIN_IDS = [int(x) for x in get("ADMIN_IDS", "883560328").split(",") if x.strip().isdigit()]


# --- Платёжка USDT (TRC20) ---
TRONSCAN_API_KEY = get("TRONSCAN_API_KEY")
TRX_WALLET = get("TRX_WALLET")
BASE_AMOUNT = float(get("BASE_AMOUNT", "9"))       # уникальные суммы: 9.49-9.98
FACT_AMOUNT = BASE_AMOUNT + 1                       # витринная цена (9.99 USDT)
CHECK_INTERVAL = get_int("CHECK_INTERVAL", 30)      # сек между проверками TronScan
# TronScan держит 1 запрос в секунду на ключ и на превышение отвечает 429
# «suspended for N s». Поэтому: не чаще одного запроса в MIN_INTERVAL секунд,
# один запрос на УНИКАЛЬНЫЙ кошелёк за цикл, ответ кэшируется на CACHE_TTL.
TRON_MIN_INTERVAL = get_float("TRON_MIN_INTERVAL", 1.1)
TRON_CACHE_TTL = get_float("TRON_CACHE_TTL", 20.0)
WAITING_TIME = get_int("WAITING_TIME", 1800)        # окно ожидания платежа, сек
ORDER_EXPIRATION_SECONDS = get_int("ORDER_EXPIRATION_SECONDS", 24 * 60 * 60)
# Тарифный заказ (plan:...) ДЕРЖИТ СУММУ заказа: пока он ждёт оплаты, никто
# другой не может подписаться на тот же тариф (детект платежа - по сумме).
# Поэтому у тарифов свой, короткий срок, а брошенная заявка освобождает сумму.
PLAN_ORDER_TTL = get_int("PLAN_ORDER_TTL", 60 * 60)                 # 1 ч на оплату тарифа
PLAN_PAYMENT_GRACE = get_int("PLAN_PAYMENT_GRACE", 12 * 60 * 60)    # 12 ч ловим позднюю оплату
USE_MOCK = get("USE_MOCK", "0") == "1"              # 1 = имитация оплат (тест)
# Секрет HMAC-подписи вебхука: получатель по нему проверяет, что "оплачено"
# прислали мы, а не кто-то, узнавший order_id и адрес вебхука.
WEBHOOK_SECRET = get("WEBHOOK_SECRET")

# --- Рубеж против абуза (18.09.2026) ---
# До этого дня у API не было ни блок-листа, ни лимита частоты: суточный прогон
# 17-18.09 (example-ip + сеть x:x:x::/64) сделал 37 646 попыток
# подписки, 1 015 попыток подбора админ-ключа и 41 699 вызовов легаси-пути.
BLOCKLIST_FILE = Path(get("BLOCKLIST_FILE", str(DATA_DIR / "blocklist.json")))
RATE_SUBSCRIBE_PER_MIN = get_int("RATE_SUBSCRIBE_PER_MIN", 6)
RATE_SUBSCRIBE_PER_HOUR = get_int("RATE_SUBSCRIBE_PER_HOUR", 20)
RATE_TG_PER_MIN = get_int("RATE_TG_PER_MIN", 60)     # страница опрашивает статус раз в 3 с
RATE_API_PER_MIN = get_int("RATE_API_PER_MIN", 120)
RATE_PUBLIC_PER_MIN = get_int("RATE_PUBLIC_PER_MIN", 120)
RATE_ADMIN_PER_MIN = get_int("RATE_ADMIN_PER_MIN", 120)
# Сколько своих неоплаченных тарифных заявок может висеть с одной сети
MAX_PENDING_PER_IP = get_int("MAX_PENDING_PER_IP", 5)

# СВОИ адреса для вебхуков (18.09.2026). Наш сервер сам делает POST по
# webhook_url, который задаёт клиент. Внутренние адреса закрыты давно, но наши
# ПУБЛИЧНЫЕ (домены и IP сервера) общий гард пропускал - и через webhook_url
# нас заставляли стучаться в наши же сайты: в логах 86 POST в собственный
# /phpmyadmin/ с нашим же UA CleanTrade-Webhook/1.0. Список имён - по суффиксу
# (www.cleantrade.info тоже ловится), адресов - точным сравнением.
WEBHOOK_SELF_HOSTS = get("WEBHOOK_SELF_HOSTS",
                         "cleantrade.info,pay.cleantrade.info,apiteka.pp.ua,"
                         "pay.apiteka.pp.ua,punksnotdead.pp.ua,"
                         "strait.punksnotdead.pp.ua,signal.punksnotdead.pp.ua,"
                         "teaindigo.com")
WEBHOOK_SELF_IPS = get("WEBHOOK_SELF_IPS", "example-vps,example-ip")
# Потолок СОЗДАННЫХ заявок с одной сети в сутки на ПУБЛИЧНОЙ двери
# (/v1/subscribe - она ключа не требует). Free-лимит считает только бесплатные
# выдачи, поэтому платные заявки с вебхуком шли мимо него: 17-18.09.2026 через
# эту дверь прошло 37 646 попыток. API-дверь с ключом не считается - там у
# подписчика свой месячный лимит. 0 = выключено.
SUBSCRIBE_IP_DAILY_LIMIT = get_int("SUBSCRIBE_IP_DAILY_LIMIT", 30)

# Версия API - единый источник: её отдают /healthz и бейдж админки.
API_VERSION = get("API_VERSION", "0.3.5")


# --- CleanTrade Payment API (клиент бота: собака ест свой корм) ---
PAYMENT_API_URL = get("PAYMENT_API_URL", "http://127.0.0.1:8080")
PAYMENT_API_KEY = get("PAYMENT_API_KEY", "")
WEBHOOK_LISTEN_PORT = get_int("WEBHOOK_LISTEN_PORT", 8081)
WEBHOOK_PATH = "/webhook/cleanprod"
WEBHOOK_URL = "http://127.0.0.1:{}{}".format(WEBHOOK_LISTEN_PORT, WEBHOOK_PATH)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
