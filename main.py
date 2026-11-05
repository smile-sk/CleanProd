"""CleanProd - единая точка входа.

Один процесс, один сервис: два Telegram-бота + локальный приёмник вебхуков
от CleanTrade Payment API + 4 сигнальщика. Запуск:

    python main.py

Платёжный конвейер (собака ест свой корм):
    sales-бот создаёт заказ через payment_api (127.0.0.1:8080),
    API поллит TronScan (единственный поллер на всю систему) и при оплате
    шлёт вебхук сюда (127.0.0.1:8081),
    приёмник находит покупателя по order_id и выдаёт zip + токен.

Секреты/настройки - в .env (через config). Состояние - в data/.
"""
import asyncio
import logging
import sys
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: F401  (инициализация путей/окружения)
from core.log import setup_logger

log = setup_logger("main", "main.log")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)


async def _run():
    from bots import access, sales, signals

    sales_app = sales.build_app()
    access_app = access.build_app()

    # --- Локальный приёмник вебхуков от CleanTrade Payment API ---
    async def webhook_handler(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="bad json")
        if data.get("status") != "paid":
            return web.Response(status=200, text="ignored")
        order_id = data.get("order_id")
        if not order_id:
            return web.Response(status=200, text="no order_id")
        chat_id = sales.chat_id_for_order(order_id)
        if not chat_id:
            log.warning("Webhook: заказ %s не найден в боте", order_id)
            return web.Response(status=200, text="unknown order")
        db = sales.load_db()
        token = await sales.grant_access(sales_app.bot, chat_id, db)
        if token:
            log.info("Webhook: оплата заказа %s обработана, токен выдан %s", order_id, chat_id)
        else:
            log.error("Webhook: не удалось выдать доступ по заказу %s", order_id)
        return web.Response(status=200, text="ok")

    async def run_webhook_server(port: int) -> None:
        app = web.Application()
        app.router.add_post(config.WEBHOOK_PATH, webhook_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        log.info("Webhook-приёмник: http://127.0.0.1:%s%s", port, config.WEBHOOK_PATH)
        while True:
            await asyncio.sleep(3600)

    log.info("CleanProd стартует: 2 бота + вебхук-приёмник + 4 сигнальщика")

    # PTB v20: для нескольких приложений в одном цикле используем
    # initialize -> updater.start_polling -> start (как в custom run_polling).
    await sales_app.initialize()
    await access_app.initialize()
    await sales_app.updater.start_polling(allowed_updates=["message", "callback_query"])
    await access_app.updater.start_polling(allowed_updates=["message", "callback_query"])
    await sales_app.start()
    await access_app.start()

    webhook_task = asyncio.create_task(run_webhook_server(config.WEBHOOK_LISTEN_PORT))

    try:
        await signals.run_all()
    finally:
        webhook_task.cancel()
        await sales_app.stop()
        await sales_app.shutdown()
        await access_app.stop()
        await access_app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Остановлено вручную (Ctrl+C)")
