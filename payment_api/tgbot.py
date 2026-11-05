"""Отправка сообщений в Telegram от платёжки (v0.3.5).

ВАЖНО про токен: слушатель апдейтов (getUpdates) у бота только один -
support-engine. Если бы платёжка начала читать апдейты тем же токеном,
Telegram отвечал бы 409 Conflict и апдейты терялись бы между процессами.
Поэтому здесь ровно одна функция - sendMessage: обычный HTTPS-запрос,
который слушателя не задевает.

Токен берётся из config.SUPPORT_BOT_TOKEN (.env: SUPPORT_BOT_TOKEN=...).
Токена нет - молчим и пишем в лог: подписка не должна падать из-за того,
что бот недоступен. Ключ всё равно виден на сайте и в GET /v1/tg/status.

Все тексты наружу - АНГЛИЙСКИЕ (правило проекта: язык по умолчанию EN).
"""
import html
import json
import logging
import urllib.parse
import urllib.request

import config
from payment_api import events

log = logging.getLogger("stablepay.tgbot")

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20


def enabled() -> bool:
    return bool(config.get("SUPPORT_BOT_TOKEN", "").strip())


def send(chat_id, text: str, keyboard: str = "") -> bool:
    """Отправить сообщение человеку. False - не вышло (не критично)."""
    token = config.get("SUPPORT_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        log.info("tgbot: токена нет или chat_id пуст - сообщение не отправлено")
        return False
    params = {
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if keyboard:
        params["reply_markup"] = keyboard
    try:
        req = urllib.request.Request(
            API.format(token=token, method="sendMessage"),
            data=urllib.parse.urlencode(params).encode(),
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
        if not data.get("ok"):
            events.log_event("tg_send_failed", chat_id=chat_id,
                             error=str(data.get("description"))[:200])
            return False
        return True
    except Exception as exc:
        events.log_event("tg_send_failed", chat_id=chat_id, error=str(exc)[:200])
        log.warning("tgbot: не отправил сообщение в %s: %s", chat_id, exc)
        return False


def key_message(api_key: str, plan_name: str, expires_at: str = "",
                docs_url: str = "https://pay.cleantrade.info/integrate.html",
                webhook_secret: str = "") -> str:
    """Текст «вот ваш ключ» - один вид на все случаи."""
    exp = ""
    if expires_at:
        exp = "\nValid until: <b>{}</b>".format(html.escape(str(expires_at)[:10]))
    secret = ""
    if webhook_secret:
        secret = ("\n\n<b>Webhook signing secret</b> (yours only):\n"
                  "<code>{}</code>\n"
                  "We sign every webhook with it: X-CleanTrade-Signature. "
                  "Never share it.").format(html.escape(webhook_secret))
    return (
        "🔑 <b>Your CleanTrade API key</b>\n\n"
        "Plan: <b>{plan}</b>{exp}\n"
        "Key:\n<code>{key}</code>{secret}\n\n"
        "Keep it private - it is your merchant key. "
        "Docs & examples: {docs}\n"
        "Commands: /key - send the key again · /plans - plans · /support - talk to a human"
    ).format(plan=html.escape(plan_name or "Free"), exp=exp,
             key=html.escape(api_key), secret=secret, docs=docs_url)
