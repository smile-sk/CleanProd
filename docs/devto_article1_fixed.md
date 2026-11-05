# How to accept USDT in Telegram bots without KYC in 10 minutes

If you run a Telegram bot that sells anything - subscriptions, digital goods,
services - you've hit the same wall: Stripe and PayPal ban crypto and
bot-related businesses, classic crypto gateways take a percentage of every payment and
hold your funds until payout, and KYC verification takes days.

There is a simpler way: **a non-custodial USDT (TRC20) payment gateway with a
flat subscription fee and 0% per-transaction fees.**

In this tutorial you'll build a Telegram bot that accepts USDT payments
end-to-end in about 10 minutes. Every code snippet is real and runs against
the live API - the same code lives in the open-source SDK.

---

## The idea: non-custodial means money goes straight to you

CleanTrade is a payment gateway that works without a middleman holding your
funds:

- The customer pays **directly to your TRC20 wallet** - you never share
  custody of your money
- **No KYC**, no registration documents, no approval queue - API key in
  1 minute
- **0% commission** - a flat subscription instead of a percentage cut
- Payment detection in **~30 seconds** via unique-amount matching

How does detection work without smart contracts? Each order gets a **unique
amount** (e.g. `9.57` USDT instead of `10.00`). When that exact amount lands
on your wallet, the poller matches it to the order and fires a **webhook**
with the full payload. Elegant, cheap, and no gas fees on top.

---

## 1. Get an API key

1. Go to [pay.cleantrade.info/subscribe.html](https://pay.cleantrade.info/subscribe.html)
2. Pick a plan (there's a free tier - enough to test)
3. Copy your API key

That's it. No documents, no waiting.

---

## 2. Create an order

```bash
curl -X POST https://pay.cleantrade.info/v1/orders \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "premium", "amount": 9.57}'
```

Response:

```json
{
  "order_id": "ord_8f3a...",
  "product_id": "premium",
  "amount": 9.57,
  "wallet": "TJ7...K9",
  "status": "created",
  "created_at": "2026-08-22T12:00:00Z"
}
```

You show the customer the `wallet` address and the exact `amount`. When they
send it, the poller detects the transaction and your webhook fires.

---

## 3. The complete Telegram bot (Python + aiogram)

Here's a minimal bot that sells digital goods. When a user taps **Buy**, the
bot creates an order, shows the wallet and amount, and waits for the webhook
to deliver the product.

```python
import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from cleantrade import Client

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API_KEY = os.environ["CLEANTRADE_API_KEY"]

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
client = Client(API_KEY)

@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Hi! I sell the SuperIndicator for USDT (TRC20).\n"
        "No registration, no KYC - you send crypto straight to my wallet\n"
        "and the bot detects it in ~30 seconds.\n\n"
        "Type /buy to get the payment details."
    )

@dp.message(F.text == "/buy")
async def buy(message: Message) -> None:
    order = client.create_order(
        product_id="indicator.zip",
        customer_ref=str(message.from_user.id),
    )
    await message.answer(
        f"Send **exactly {order.amount} USDT** (TRC20) to:\n\n"
        f"`{order.wallet}`\n\n"
        f"⚠️ Send the exact amount - the payment is matched by it.\n"
        f"⏳ Detection takes ~30 seconds. I'll notify you here.",
        parse_mode="Markdown",
    )

async def main() -> None:
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

Install dependencies:

```bash
pip install aiogram cleantrade
```

The full version with automatic delivery on payment is in the repo:
`examples/telegram_bot/bot.py`.

---

## 4. Webhook server: deliver the product automatically

Create a small FastAPI app that receives payment notifications and releases
the goods:

```python
from fastapi import FastAPI, Request

from cleantrade import make_fastapi_handler, WebhookPayload

app = FastAPI()

def deliver(p: WebhookPayload) -> None:
    if not p.is_paid:
        return
    print(f"[PAID] order={p.order_id} product={p.product_id} "
          f"tx={p.tx_id} amount={p.amount}")
    # deliver your product here - p.customer_ref identifies the buyer

@app.post("/webhook")
async def webhook(request: Request) -> str:
    return make_fastapi_handler(deliver)(await request.body())
```

Pass the webhook URL when creating the order
(`webhook_url="https://your.domain/webhook"`), and payments become fully
automatic: customer pays → webhook fires (with retries up to 5 times until
your server answers 2xx) → product delivered. No polling code on your side
needed.

---

## 5. What it costs

| Plan | Monthly | Yearly (12 months for the price of 10) | Orders/mo |
| --- | --- | --- | --- |
| Free | $0 | - | 5 |
| Starter | $9.99 | $99.90 | 100 |
| Pro | $29.99 | $299.90 | 3,000 |
| Business | $79.99 | $799.90 | Unlimited |

Compare with classic gateways: at 0.5% (NOWPayments, Plisio), $10,000/month
of volume costs **~$50 in fees** - close to CleanTrade's flat $29.99 Pro. The
real reason to switch is custody and DX: money lands on your wallet
instantly, no gateway balance, no KYC queue.

---

## Why this matters

- **No bans**: USDT on your own wallet can't be frozen by a payment processor
- **No KYC wall**: your customers stay private, you stay private
- **0% fees**: flat subscription, the math is predictable
- **~30 sec detection**: webhooks with up to 5 retries, full `tx_id` payload

The SDK is open source:
[github.com/smile-sk/cleantrade-python](https://github.com/smile-sk/cleantrade-python)
- includes the client, webhook server example, and this Telegram bot.

Full docs: [pay.cleantrade.info](https://pay.cleantrade.info/) ·
API reference: [pay.cleantrade.info/docs](https://pay.cleantrade.info/docs)

Happy building! 🚀
