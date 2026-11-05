# Building a non-custodial paywall in Python with TRC20 webhooks

Selling digital goods means solving one hard problem: **how do you get paid
without a middleman that can freeze you, ban you, or take a cut?**

For Telegram bots and micro-SaaS, the usual answers are painful:

- **Stripe/PayPal** - ban crypto and bot-related businesses outright
- **Custodial crypto gateways** - hold your funds, take a cut, KYC queue
- **Smart contracts** - powerful, but you pay gas on every payment and your
  buyers need dApp experience

There's a simpler pattern: **unique-amount matching on a plain TRC20 wallet**.
No smart contracts. No custody. No gas. This is the architecture behind
CleanTrade, and it's fully reproducible.

## How unique-amount matching works

Every order gets a **unique amount** - e.g. `9.57` USDT instead of a round
`10.00`. The buyer sends exactly that amount to your wallet address. Your
poller watches the chain, sees a transfer of `9.57`, matches it to the order,
and fires a **webhook** with the transaction ID.

Why not just watch for *any* transfer? Because with unique amounts you don't
need per-order addresses (expensive to generate on TRON) and you don't need
smart contracts (no gas). The amount *is* the identifier.

## Step 1: create an order

The API returns a unique amount and your wallet:

```python
from cleantrade import Client

client = Client("sp_live_YOUR_KEY")

order = client.create_order(
    product_id="indicator.zip",
    customer_ref="user123",
    webhook_url="https://my.site/hook",   # optional, see step 3
)

print(f"Ask the buyer to send exactly {order.amount} USDT (TRC20) to {order.wallet}")
```

Response (real payload, `POST /v1/orders`):

```json
{
  "order_id": "ord_8f3a...",
  "product_id": "indicator.zip",
  "amount": 9.57,
  "wallet": "TJ7...K9",
  "status": "created",
  "created_at": "2026-08-23T10:00:00Z"
}
```

## Step 2: poll until paid (simplest pattern)

No public URL needed - perfect for a bot behind NAT:

```python
import time
from cleantrade import Client

client = Client("sp_live_YOUR_KEY")
order = client.create_order("indicator.zip", customer_ref="user123")

while True:
    order = client.get_order(order.order_id)
    if order.is_paid:
        print(f"PAID! tx_id={order.tx_id} - deliver the goods now.")
        break
    if order.is_expired:
        print("Order expired (24h window). Create a new one.")
        break
    time.sleep(5)
```

That's `examples/poll_status.py` in the repo, verbatim.

## Step 3: webhooks (recommended for production)

When the poller detects the payment, CleanTrade POSTs a JSON payload to your
`webhook_url` and **retries up to 5 times** until your server answers 2xx.
The full receiver is `examples/webhook_server.py`:

```python
from fastapi import FastAPI, Request

from cleantrade import make_fastapi_handler, WebhookPayload

app = FastAPI(title="CleanTrade webhook receiver")

GOODS = {
    "indicator.zip": "https://dl.example.com/indicator.zip",
}

def deliver(p: WebhookPayload) -> None:
    """Your business logic: unlock the purchase for the buyer."""
    if not p.is_paid:
        return
    product = GOODS.get(p.product_id)
    print(f"[PAID] order={p.order_id} product={p.product_id} tx={p.tx_id} "
          f"amount={p.amount} -> deliver {product}")
    # Send the download link / key to the buyer here (Telegram, email, ...).
    # Use p.customer_ref to identify the buyer.

@app.post("/hook")
async def hook(request: Request) -> str:
    return make_fastapi_handler(deliver)(await request.body())
```

What the webhook actually delivers (real payload from the API):

```json
{
  "order_id": "ord_8f3a...",
  "product_id": "indicator.zip",
  "amount": 9.57,
  "status": "paid",
  "tx_id": "70e07357b23cd5a68bdf83746a74459dd6a7cb62b92254ff73ff065046b9c773",
  "paid_at": "2026-08-23T10:01:00Z"
}
```

`tx_id` is the on-chain transaction hash - your proof of payment.

## Order lifecycle

| Status | Meaning |
| --- | --- |
| `created` | Waiting for payment (valid 24h) |
| `paid` | Payment detected, `tx_id` available |
| `expired` | The 24h window passed |

## Error handling that actually happens

The SDK maps HTTP errors to exceptions:

| HTTP | Exception | When |
| --- | --- | --- |
| 400 | `BadRequestError` | Invalid request (e.g. unknown plan) |
| 401 | `UnauthorizedError` | Missing / invalid API key |
| 402 | `LimitReachedError` | Monthly plan limit exceeded - upgrade |
| 404 | `NotFoundError` | Order not found |
| 409 | `ConflictError` | Amount busy / no free unique amounts right now |

## Why this matters for your bot

- **Non-custodial**: the money goes straight to *your* wallet. No one can
  freeze it, no one holds it overnight.
- **No KYC for your buyers**: they just send USDT. No forms, no documents.
- **No gas**: unique-amount matching costs nothing extra per payment.
- **~30 second detection**: the poller checks the chain on a short interval.

## The full architecture in one sentence

`POST /v1/orders` → buyer sends a unique amount to your wallet → poller
matches it on-chain → webhook with `tx_id` → your code delivers the goods.

The SDK and all examples are open source:
[github.com/smile-sk/cleantrade-python](https://github.com/smile-sk/cleantrade-python)

API reference: [pay.cleantrade.info/docs](https://pay.cleantrade.info/docs) ·
OpenAPI: [pay.cleantrade.info/openapi.json](https://pay.cleantrade.info/openapi.json)
