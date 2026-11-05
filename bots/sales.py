"""

CleanTrade Sales Bot (@CleanTrade_bot)

======================================

Продаёт CleanTrail (индикатор на Pine Script + PDF-гайды) за 9.99 USDT (TRC20).



Платёжный конвейер - через CleanTrade Payment API (собака ест свой корм):

    /buy -> POST /v1/orders на локальный API (127.0.0.1:8080)

        -> API сам генерирует УНИКАЛЬНУЮ сумму (9.49-9.98 USDT) и отдаёт кошелёк

    -> покупатель шлёт ровно эту сумму на кошелёк

    -> API поллит TronScan (единственный поллер на всю систему)

    -> совпадение суммы = оплата -> вебхук на локальный приёмник (main.py)

    -> приёмник вызывает grant_access: выдача zip + токена доступа

    -> токен активирует доступ в гейт-боте (@HexusCrypto_bot)



Секреты и настройки - в .env (через config). Состояние - в data/ (gitignored).

"""

import json

import logging

import time

from collections import Counter

from datetime import datetime

from typing import Optional



import requests



import config

from core import auth as token_store

from core.log import setup_logger



from telegram import (

    Bot,

    InlineKeyboardButton,

    InlineKeyboardMarkup,

    Update,

)

from telegram.ext import (

    ApplicationBuilder,

    CallbackQueryHandler,

    CommandHandler,

    ContextTypes,

    MessageHandler,

    filters,

)



config.ensure_dirs()

logger = setup_logger("cleanpay_bot", "pay_bot.log")



logging.getLogger("httpx").setLevel(logging.WARNING)

logging.getLogger("telegram").setLevel(logging.WARNING)





class PaymentApiClient:

    """Клиент CleanTrade Payment API (локально, 127.0.0.1:8080).



    Бот - первый мерчант нашего же API: создаёт заказ, спрашивает статус.

    """



    def __init__(self, base_url: str, api_key: str):

        self.base_url = base_url.rstrip("/")

        self.api_key = api_key

        self._timeout = 10



    def _headers(self) -> dict:

        return {"X-API-Key": self.api_key} if self.api_key else {}



    def create_order(self, product_id: str, webhook_url: str) -> Optional[dict]:

        try:

            resp = requests.post(

                self.base_url + "/v1/orders",

                json={"product_id": product_id, "wallet": config.TRX_WALLET,
                      "webhook_url": webhook_url},

                headers=self._headers(),

                timeout=self._timeout,

            )

        except Exception as exc:

            logger.error("API create_order error: %s", exc)

            return None

        if resp.status_code != 201:

            logger.error("API create_order HTTP %s: %s", resp.status_code, resp.text[:200])

            return None

        return resp.json()



    def get_order(self, order_id: str) -> Optional[dict]:

        try:

            resp = requests.get(

                self.base_url + "/v1/orders/" + order_id,

                headers=self._headers(),

                timeout=self._timeout,

            )

        except Exception as exc:

            logger.error("API get_order error: %s", exc)

            return None

        if resp.status_code != 200:

            return None

        return resp.json()





api = PaymentApiClient(config.PAYMENT_API_URL, config.PAYMENT_API_KEY)



# === Клавиатуры ===

main_menu_keyboard = InlineKeyboardMarkup([

    [InlineKeyboardButton("📄 What's Included", callback_data="details")],

    [InlineKeyboardButton("🖼️ Monitoring Bot Screens", callback_data="screens_bot")],

    [InlineKeyboardButton("📈 Indicator Screens", callback_data="screens_indicator")],

    [InlineKeyboardButton("💰 Buy Now", callback_data="buy")],

])



back_keyboard = InlineKeyboardMarkup([

    [InlineKeyboardButton("⬅️ Back", callback_data="back_to_main")]

])





# === Заказы (data/orders.json) ===

def load_db() -> dict:

    if not config.ORDERS_FILE.exists():

        return {}

    with open(config.ORDERS_FILE, "r", encoding="utf-8") as fh:

        return json.load(fh)





def save_db(db: dict) -> None:

    with open(config.ORDERS_FILE, "w", encoding="utf-8") as fh:

        json.dump(db, fh, indent=2)





def generate_token() -> str:

    return token_store.issue_token()





def load_token(chat_id) -> Optional[str]:

    db = load_db()

    info = db.get(str(chat_id))

    return info.get("token") if info else None





def chat_id_for_order(order_id: str) -> Optional[str]:

    """Вебхук приносит order_id API - ищем, какому чату он принадлежит."""

    db = load_db()

    for chat_id, info in db.items():

        if info.get("order_id") == order_id:

            return chat_id

    return None





def access_link(token: str) -> str:

    return f"https://t.me/{config.ACCESS_BOT_USERNAME}?start=auth_{token}"





async def grant_access(bot: Bot, chat_id: str, db: dict) -> Optional[str]:

    """Выдать токен + zip (один раз), уведомить админа. Возвращает токен."""

    info = db.get(chat_id)

    existing = info.get("token") if info else None

    token = existing or generate_token()



    if not existing:

        try:

            with open(config.ARCHIVE_PATH, "rb") as fh:

                await bot.send_document(chat_id=int(chat_id), document=fh, filename="CleanTrail.zip")

            await bot.send_message(

                chat_id=int(chat_id),

                text=(

                    "✅ Payment received!\n"

                    "Your access token:\n"

                    f"<code>{token}</code>\n\n"

                    f"Open 👉 {access_link(token)}"

                ),

                parse_mode="HTML",

            )

        except Exception as exc:

            logger.error("Не удалось отправить zip/токен пользователю %s: %s", chat_id, exc)

            return None



    if info is not None:

        info["status"] = "paid"

        info["token"] = token

        save_db(db)



    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    amount = info.get("amount", 0) if info else 0

    for admin_id in config.ADMIN_IDS:

        try:

            await bot.send_message(

                chat_id=admin_id,

                text=(

                    f"💰 *New payment received!*\n"

                    f"User ID: `{chat_id}`\n"

                    f"Amount: *{amount} USDT*\n"

                    f"Time: `{timestamp}`\n\n"

                    f"📦 Archive and token sent."

                ),

                parse_mode="Markdown",

            )

        except Exception as exc:

            logger.error("Admin notify error: %s", exc)

    return token





# === Команды ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    user_id = user.id

    token_store.unblock(user_id)  # написал боту - значит снова доступен

    username = user.username or "NoUsername"

    first_name = user.first_name or ""

    last_name = user.last_name or ""

    full_name = f"{first_name} {last_name}".strip()



    args = context.args

    source = args[0] if args else "organic"



    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_line = f"[{timestamp}] /start by {user_id} ({username}) {full_name} | source: {source}\n"

    try:

        with open(config.STAT_FILE, "a", encoding="utf-8") as fh:

            fh.write(log_line)

    except Exception as exc:

        logger.error("Не удалось записать stat.log: %s", exc)



    await update.message.reply_text(

        "<b>🚀 CleanTrade - Your Smart Start to Crypto Trading</b>\n\n"

        "🎯 <b>Perfect for beginners</b>: confirmed signals, easy setup, lifetime access.\n"

        "📦 <b>What you get</b>: CleanTrail Pine Script indicator v4 + Telegram screener bot with real-time market monitoring + PDF guides in 7 languages.\n"

        "📊 <b>Learn to read the market</b> - understand structure, spot momentum, and time your entries.\n\n"

        f"<b>💰 One-time payment - only {config.FACT_AMOUNT} USDT (TRC20)</b>\n"

        "✅ No subscriptions. No noise. Just lifetime value.\n\n"

        "<b>👉 Use /buy to get started</b>\n"

        "ℹ️ Need help? Use /help\n"

        "Want to know more? Tap a button below 👇",

        reply_markup=main_menu_keyboard,

        parse_mode="HTML",

    )





async def details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.callback_query.answer()

    await update.callback_query.message.edit_text(

        f"<b>📦 CleanTrade Lifetime Access - {config.FACT_AMOUNT} USDT</b>\n\n"

        "You'll get access to two powerful tools:\n\n"

        "📈 <b>CleanTrail Indicator v4 (.pine)</b>\n\n"
        "• ATR-normalized signals - every coin measured against its own volatility\n"
        "• Sideways filter (Kaufman Efficiency Ratio) - no spam in flat markets, strong breakouts still pass\n"
        "• HMA trend filter + RVOL volume confirmation + RSI divergence detection\n"
        "• Adaptive ATR trailing stop + Swing Fibonacci targets (38.2 / 61.8 / 100)\n"
        "• Support & Resistance zones and pivot levels on-chart\n"
        "• Entry/exit markers with TP/SL labels\n"
        "• Includes PDF user & installation guides in 7 languages\n\n"

        "🤖 <b>CleanTrade Screener Bot</b>\n\n"
        "Real-time alerts (1-hour timeframe):\n\n"
        "• Impulse Moves - ATR-normalized price + RVOL volume + OI patterns (conviction / squeeze)\n"
        "• Fair Value Gaps (FVG)\n"
        "• Liquidations over $100,000\n"
        "• Session Open/Close timings\n\n"

        "🔍 Monitored pairs: BTC, ETH, SOL, XRP, DOGE, PEPE, LDO, ADA, BNB, WIF, TON, LINK, ENA, LTC, ONDO\n\n"

        "📁 Package includes .zip with the indicator file + PDF guides in 7 languages\n\n"

        "<b>🎯 Designed for beginners</b> - simple tools and extra strategy tips in the guide to help you start trading with confidence.\n\n"

        "⚠️ CleanTrail indicator and CleanTrade screener is a technical analysis tools. It does not guarantee profit or constitute financial advice. Use at your own discretion.",

        reply_markup=back_keyboard,

        parse_mode="HTML",

    )





async def _send_screenshots(update: Update, screenshots):

    for filepath, caption in screenshots:

        with open(filepath, "rb") as photo:

            await update.callback_query.message.reply_photo(photo, caption=caption)





async def screens_bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.callback_query.answer()

    await update.callback_query.message.edit_text(

        "🖼️ <b>CleanTrade Screener Bot</b>\n\n"
        "Live examples of how the CleanTrade bot works:\n\n"
        "🔹 Session open/close alerts (Asia, EU, US)\n"
        "🔹 Impulse alerts on the 1-hour timeframe - ATR-normalized moves with RVOL volume and OI patterns\n"
        "🔹 Fair Value Gap (FVG) alerts on the 1-hour timeframe\n"
        "🔹 Large liquidation alerts over $100,000\n"
        "🔹 Sideways filter - no noise in flat markets, strong breakouts still pass\n\n"

        "All signals are delivered in real-time directly to your Telegram.\n\n"

        "⚙️ No configuration needed - plug and play.\n"

        "📌 Use /buy to get lifetime access now.",

        parse_mode="HTML",

        reply_markup=back_keyboard,

    )

    await _send_screenshots(update, [

        (config.MEDIA_DIR / "sessions.png", "🕒 Session Open/Close Alert"),

        (config.MEDIA_DIR / "liq.png", "💥 $100K+ Liquidation Alert"),

        (config.MEDIA_DIR / "bullish_alert.png", "📈 Bullish Impulse Signal"),

        (config.MEDIA_DIR / "bearish_alert.png", "📉 Bearish Impulse Signal"),

        (config.MEDIA_DIR / "bullish_fvg.png", "🔹 Bullish Fair Value Gap"),

        (config.MEDIA_DIR / "bearish_fvg.png", "🔸 Bearish Fair Value Gap"),

    ])





async def screens_indicator_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.callback_query.answer()

    await update.callback_query.message.edit_text(

        "📈 <b>CleanTrail Indicator v4 - Screenshots</b>\n\n"
        "Visual confirmations, trend following, and strategy layout:\n\n"
        "• Entry/Exit markers with TP/SL labels\n"
        "• RSI divergence signals\n"
        "• Adaptive ATR trailing stop\n"
        "• Swing Fibonacci targets\n"
        "• Sideways filter (Kaufman ER) with breakout exception\n"
        "• PDF user & installation guides in 7 languages\n\n"

        "All in one simple and clean layout on TradingView.\n"

        "📌 Use /buy to get lifetime access now.",

        reply_markup=back_keyboard,

        parse_mode="HTML",

    )

    await _send_screenshots(update, [

        (config.MEDIA_DIR / "indicator_1.png", "📍 CleanTrail Signal"),

        (config.MEDIA_DIR / "indicator_2.png", "🔁 Adaptive Trailing Stop"),

        (config.MEDIA_DIR / "indicator_3.png", "📉 EMA, RSI, Volume, ATR, Divergences"),

        (config.MEDIA_DIR / "indicator_4.png", "🆕 Support & Resistance Zones and Pivot Levels on-chart for smarter entries"),

        (config.MEDIA_DIR / "pdf_setup_guide.png", "📘 Setup Guide Preview"),

        (config.MEDIA_DIR / "pdf_strategy_guide.png", "📘 Strategy Guide Preview"),

    ])





async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = str(update.effective_chat.id)

    db = load_db()



    if update.message:

        reply = update.message.reply_text

    elif update.callback_query:

        reply = update.callback_query.message.reply_text

        await update.callback_query.answer()

    else:

        return



    if chat_id in db:

        status = db[chat_id].get("status", "")

        amount = db[chat_id]["amount"]



        if status == "paid":

            await reply(

                "✅ You've already completed your purchase.\n"

                "Your access token has been issued.\n\n"

                "Use /mytoken to retrieve it again.",

                parse_mode="Markdown",

            )

            return



        if status == "waiting":

            await reply(

                f"⏳ Still waiting for your payment of *{amount} USDT* to:\n`{config.TRX_WALLET}`",

                parse_mode="Markdown",

            )

            return



    # Новый заказ - создаём через CleanTrade Payment API (уникальную сумму генерит API)

    order = api.create_order("cleantrail", config.WEBHOOK_URL)

    if order is None:

        await reply(

            "⚠️ Payment service is temporarily unavailable.\n"

            "Please try again in a few minutes.",

            parse_mode="Markdown",

        )

        return



    amount = order["amount"]

    order_id = order["order_id"]

    wallet = order["wallet"]



    db[chat_id] = {"order_id": order_id, "amount": amount, "created_at": time.time(), "status": "waiting"}

    save_db(db)



    logger.info("Новый заказ %s от %s на %s USDT (через API)", order_id, chat_id, amount)

    await reply(

        f"<b>💰 Buy CleanTrade Lifetime Access - {config.FACT_AMOUNT} USDT</b>\n\n"

        "✅ One-time payment (USDT, TRC20)\n"

        "✅ Instant access after payment\n"

        "✅ Includes .zip download + your personal access token\n\n"

        "🪙 <b>Payment instructions:</b>\n"

        f"1. Send <b>exactly <code>{amount} USDT</code></b> to the wallet address below\n"

        "2. After confirmation, you'll get your access instantly\n\n"

        "🔹 <b>Network:</b> TRON (TRC20)\n"

        f"🔹 <b>Address:</b>\n<b><code>{wallet}</code></b>\n\n"

        "⚠️ The amount must match exactly - it's unique to your order.\n"

        "🧩 This value helps us detect and process your payment automatically.\n"

        "💡 Cover all network fees when sending (e.g., select “I pay all fees” on Binance / OKX).\n\n"

        "Need help? Use /help",

        reply_markup=back_keyboard,

        parse_mode="HTML",

    )



    screenshot = config.MEDIA_DIR / "download.png"

    if screenshot.exists() and update.message:

        with open(screenshot, "rb") as photo:

            await update.message.reply_photo(

                photo=photo,

                caption="💾 After payment confirmation, you will receive a message with a download link to the archive and a personal access token for screener access.",

            )





async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = str(update.effective_chat.id)

    db = load_db()

    info = db.get(chat_id)



    if not info:

        await update.message.reply_text("❌ No payment info found. Use /buy to start.")

        return



    if info["status"] == "paid":

        await update.message.reply_text("✅ Your payment is confirmed.")

        return



    if time.time() - info["created_at"] > config.WAITING_TIME:

        await update.message.reply_text("⌛ Your payment session expired. Please use /buy to start again.")

        return



    order = api.get_order(info.get("order_id", ""))

    if order is None:

        await update.message.reply_text(

            "⚠️ Payment service is temporarily unavailable.\n"

            "Please try again in a few minutes.",

            parse_mode="Markdown",

        )

        return



    if order["status"] == "paid":

        token = await grant_access(context.bot, chat_id, db)

        if token:

            await update.message.reply_text(

                "✅ Payment received!\n"

                f"Your access token:\n<code>{token}</code>\n\n"

                f"Open 👉 {access_link(token)}",

                parse_mode="HTML",

            )

    elif order["status"] == "expired":

        await update.message.reply_text("⌛ Your payment session expired. Please use /buy to start again.")

    else:

        await update.message.reply_text(

            f"⏳ Still waiting for *{info['amount']} USDT* payment.",

            parse_mode="Markdown",

        )





async def mytoken(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat_id = str(update.effective_chat.id)

    token = load_token(chat_id)

    if token:

        await update.message.reply_text(

            f"🎟️ Your token: <code>{token}</code>\n\nOpen 👉 {access_link(token)}",

            parse_mode="HTML",

        )

    else:

        await update.message.reply_text("❌ No token found. Make sure your payment is confirmed.")





# === Админ-команды ===

async def manualtoken(update: Update, context: ContextTypes.DEFAULT_TYPE):

    admin_id = update.effective_chat.id

    if admin_id not in config.ADMIN_IDS:

        await update.message.reply_text("⛔ Access denied.")

        return



    if len(context.args) != 1:

        await update.message.reply_text("Usage: /manualtoken <user_chat_id>")

        return



    target_id = context.args[0]

    db = load_db()



    token = generate_token()

    link = access_link(token)



    try:

        await context.bot.send_message(

            chat_id=int(target_id),

            text=(

                "🎁 Your access has been activated!\n\n"

                f"🔑 Personal access token:\n<code>{token}</code>\n\n"

                f"🔗 Access the screener here:\n{link}"

            ),

            parse_mode="HTML",

        )

        logger.info("Токен отправлен пользователю %s", target_id)

    except Exception as exc:

        logger.error("Не удалось отправить токен %s: %s", target_id, exc)



    db[target_id] = {"status": "paid", "token": token, "amount": 0, "created_at": time.time()}

    save_db(db)



    await update.message.reply_text(

        f"✅ Manual access granted\n\nUser: `{target_id}`\nToken: `{token}`\n\n🔗 {link}",

        parse_mode="Markdown",

    )

    logger.info("Ручная выдача токена %s админом %s", target_id, admin_id)





async def manualzip(update: Update, context: ContextTypes.DEFAULT_TYPE):

    admin_id = update.effective_chat.id

    if admin_id not in config.ADMIN_IDS:

        await update.message.reply_text("⛔ Access denied.")

        return



    if len(context.args) != 1:

        await update.message.reply_text("Usage: /manualzip <user_id>")

        return



    target_id = int(context.args[0])

    try:

        with open(config.ARCHIVE_PATH, "rb") as fh:

            await context.bot.send_document(

                chat_id=target_id,

                document=fh,

                caption="📦 Here is your CleanTrail indicator archive.",

            )

        logger.info("ZIP отправлен пользователю %s", target_id)

        await update.message.reply_text(f"✅ ZIP sent to user {target_id}")

    except Exception as exc:

        logger.error("Не удалось отправить ZIP %s: %s", target_id, exc)

        await update.message.reply_text(f"⚠️ Failed to send ZIP to user {target_id}: {exc}")





async def logfile(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.id not in config.ADMIN_IDS:

        await update.message.reply_text("⛔ Access denied.")

        return

    path = config.LOG_DIR / "pay_bot.log"

    if path.exists():

        with open(path, "rb") as fh:

            await update.message.reply_document(fh)





async def statfile(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.id not in config.ADMIN_IDS:

        await update.message.reply_text("⛔ Access denied.")

        return

    if config.STAT_FILE.exists():

        with open(config.STAT_FILE, "rb") as fh:

            await update.message.reply_document(fh)





async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id not in config.ADMIN_IDS:

        await update.message.reply_text("⛔️ You are not authorized to view statistics.")

        return



    if not config.STAT_FILE.exists():

        await update.message.reply_text("⚠️ No stats available yet.")

        return



    with open(config.STAT_FILE, "r", encoding="utf-8") as fh:

        lines = fh.readlines()



    total_starts = len(lines)

    unique_users = set()

    today_starts = 0

    source_counter = Counter()



    today_str = datetime.now().strftime("%Y-%m-%d")



    for line in lines:

        parts = line.strip().split("by")

        if len(parts) < 2:

            continue

        user_part = parts[1].split("(")[0].strip()

        user_id_logged = user_part.split()[0]

        unique_users.add(user_id_logged)



        if today_str in line:

            today_starts += 1



        if "source:" in line:

            source = line.split("source:")[-1].strip()

            source_counter[source] += 1



    top_sources = "\n".join([f"• {src}: {cnt}" for src, cnt in source_counter.most_common()])



    await update.message.reply_text(

        f"📊 <b>CleanTrade Bot Stats</b>\n\n"

        f"👥 Total /start runs: <b>{total_starts}</b>\n"

        f"🧑‍💻 Unique users: <b>{len(unique_users)}</b>\n"

        f"📅 /start today: <b>{today_starts}</b>\n\n"

        f"🔍 Top sources:\n{top_sources if top_sources else '-'}",

        parse_mode="HTML",

    )





async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.id not in config.ADMIN_IDS:

        await update.message.reply_text("⛔ Access denied.")

        return



    await update.message.reply_text(

        "<b>🛠 Admin Commands:</b>\n\n"

        "✨ <b>/start</b> - get start\n"

        "🧩 <b>/manualtoken</b> - token generator\n"

        "📦 <b>/manualzip</b> - give indicator ZIP file\n"

        "📊 <b>/stats</b> - show summary stats\n"

        "📁 <b>/logfile</b> - download main log\n"

        "📁 <b>/statfile</b> - download stat.log\n"

        "👁 <b>/admin</b> - show this message",

        parse_mode="HTML",

    )





async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    waiting_minutes = int(config.WAITING_TIME / 60)

    expiration_hours = int(config.ORDER_EXPIRATION_SECONDS / 3600)

    await update.message.reply_text(

        "🆘 *Help Menu*\n\n"

        "Here's what you can do:\n"

        "/buy - Start the purchase process\n"

        "/status - Check if your payment is confirmed\n"

        "/mytoken - Retrieve your access token\n"

        "/support - Contact our support bot if something went wrong\n\n"

        "💳 Send USDT (TRC20) using /buy. Make sure to send the *exact* amount (it's unique to your order). This value helps us detect and process your payment automatically. Once confirmed, you'll get the indicator and token automatically.\n\n"

        f"⏱️ Payments are checked automatically for {waiting_minutes} minutes.\n\n"

        "If you don't pay within this time, please use /status to check manually after completing your payment.\n\n"

        f"If the payment is not completed within {expiration_hours} hours, the request will expire. You can start a new one anytime using /buy.",

        parse_mode="Markdown",

        reply_markup=back_keyboard,

    )





async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 Need help?\n"
        "Our support bot will assist you quickly.\n"
        "Open it and describe your issue:\n"
        "https://t.me/CleanTradeSupport_bot?start=trail\n\n"
        "We'll get back to you as soon as possible.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🆘 Contact Support", url="https://t.me/CleanTradeSupport_bot?start=trail"),
        ]]),
    )


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.callback_query.answer()

    await update.callback_query.message.edit_text(

        "<b>🚀 CleanTrade - Your Smart Start to Crypto Trading</b>\n\n"

        "🎯 <b>Perfect for beginners</b>: confirmed signals, easy setup, lifetime access.\n"

        "📦 <b>What you get</b>: CleanTrail Pine Script indicator v4 + Telegram screener bot with real-time market monitoring + PDF guides in 7 languages.\n"

        "📊 <b>Learn to read the market</b> - understand structure, spot momentum, and time your entries.\n\n"

        f"<b>💰 One-time payment - {config.FACT_AMOUNT} USDT (TRC20)</b>\n"

        "✅ No subscriptions. No noise. Just lifetime value.\n\n"

        "🆕 <b>UPDATE! v4:</b> ATR-normalized impulses, sideways filter, Swing Fib targets, TP/SL labels - and PDF guides in 7 languages!\n\n"

        "<b>👉 Use /buy to get started</b>\n"

        "ℹ️ Need help? Use /help\n"

        "Want to know more? Tap a button below 👇",

        reply_markup=main_menu_keyboard,

        parse_mode="HTML",

    )





async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    token_store.unblock(update.effective_user.id)

    await update.message.reply_text(

        "❓ I didn't understand that message.\n\n"

        "Here's what you can do:\n"

        "/buy - Start the purchase process\n"

        "/status - Check if your payment is confirmed\n"

        "/mytoken - Retrieve your access token\n\n"

        "If you're unsure, type /help for full instructions.",

        parse_mode="Markdown",

        reply_markup=back_keyboard,

    )





def build_app():

    app = ApplicationBuilder().token(config.SALES_BOT_TOKEN).build()



    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("buy", buy))

    app.add_handler(CommandHandler("status", status))

    app.add_handler(CommandHandler("mytoken", mytoken))

    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(CommandHandler("manualtoken", manualtoken))

    app.add_handler(CommandHandler("manualzip", manualzip))

    app.add_handler(CommandHandler("logfile", logfile))

    app.add_handler(CommandHandler("statfile", statfile))

    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(CommandHandler("support", support))

    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(MessageHandler(filters.ALL, fallback))



    app.add_handler(CallbackQueryHandler(details_callback, pattern="^details$"))

    app.add_handler(CallbackQueryHandler(buy, pattern="^buy$"))

    app.add_handler(CallbackQueryHandler(screens_bot_callback, pattern="^screens_bot$"))

    app.add_handler(CallbackQueryHandler(screens_indicator_callback, pattern="^screens_indicator$"))

    app.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_to_main$"))



    logger.info("🚀 Продающий бот запущен")

    return app

