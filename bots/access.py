"""
CleanTrade Access Bot (@HexusCrypto_bot)
========================================
Гейт доступа: токен (выданный sales-ботом) -> авторизация пользователя.
Сигналы рассылаются из bots.signals (этот же бот @HexusCrypto_bot).

Секреты и настройки - в .env (через config). Состояние - в data/ (gitignored).
"""
import config
from core import auth as token_store

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

config.ensure_dirs()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    token_store.unblock(user_id)  # написал боту - значит снова доступен
    if token_store.is_authorized(user_id):
        await update.message.reply_text(
            "✅ You already have access.\nUse /help to see the list of available commands."
        )
        return

    args = context.args
    if args and args[0].startswith("auth"):
        await auth_by_token(update, context, args[0][5:])
    else:
        await update.message.reply_text(
            "👋 Welcome to CleanTrade Screener!\n"
            "To activate your access, use:\n"
            "`/auth <your_token>` or\n"
            "Use /help to see the list of available commands.",
            parse_mode="Markdown",
        )


async def auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗ Usage: /auth <token>")
        return
    await auth_by_token(update, context, context.args[0])


async def auth_by_token(update: Update, context: ContextTypes.DEFAULT_TYPE, token: str):
    user_id = str(update.effective_user.id)
    if token_store.is_authorized(user_id):
        await update.message.reply_text(
            "✅ You already have access.\nUse /help to see the list of available commands."
        )
        return

    if token_store.consume_token(token, user_id):
        await update.message.reply_text(
            "✅ Access granted. Welcome!\nUse /help to see the list of available commands."
        )
    else:
        await update.message.reply_text(
            "❌ Invalid or already used token.\nUse /help to see the list of available commands."
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if token_store.is_authorized(user_id):
        await update.message.reply_text(
            "✅ You have active access.\nUse /help to see the list of available commands."
        )
    else:
        await update.message.reply_text(
            "❌ You don't have access.\nUse /help to see the list of available commands."
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


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Available Commands:*\n"
        "/start - Start the bot and authorize with a token\n"
        "/auth <token> - Activate your access using a token\n"
        "/status - Check your current access status\n"
        "/support - Contact our support bot\n"
        "/help - Show this help message\n"
        "💰 *Buy access: @CleanTrade_bot*",
        parse_mode="Markdown",
    )


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token_store.unblock(update.effective_user.id)
    await update.message.reply_text(
        "❓ I didn't understand that command.\n"
        "Use /help to see the list of available commands."
    )


def build_app():
    app = ApplicationBuilder().token(config.ACCESS_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth", auth))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.COMMAND | filters.TEXT, unknown))

    return app
