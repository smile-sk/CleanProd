# Accept crypto in bots without Stripe limits: TRC20 unique amounts explained

Stripe is great until it isn't. For crypto and bot businesses the pain is
specific: **limits, freezes, and a cut of every sale**. But the alternative people
immediately think of - smart contracts - comes with its own costs: gas on
every payment, and a learning curve for your buyers.

There's a third way that most people haven't heard of: **unique-amount
matching**. It's what lets a plain USDT (TRC20) wallet accept payments with
zero smart contracts, zero gas, and detection in ~30 seconds. This post
explains exactly how it works and the edge cases that matter.

## The core trick

Instead of telling the buyer "send me $10", you give them a slightly unusual
amount: `9.57`, `9.84`, `19.889`... Each order gets its **own** amount. Your
server watches the wallet's incoming transfers, finds a transfer matching an
open order's amount, and marks it paid.

The amount is the identifier. No per-order addresses, no contracts, no extra
fees - just a number.

## Why unique amounts and not "any amount to this address"

If you only have one wallet address (which is cheap and simple on TRON), you
can't tell which buyer paid you. Unique amounts solve this without creating a
new address per order (which costs TRX and adds complexity).

The flow:

```
POST /v1/orders
    → { order_id, amount: 9.57, wallet: TJ7...K9, status: created }

buyer sends 9.57 USDT to TJ7...K9

poller checks the chain (~30s interval)
    → finds transfer of 9.57
    → matches order, status: paid
    → webhook with tx_id
```

## The edge case that bites: 6 decimal places

USDT on TRC20 carries **6 decimal places**. The buyer might send `9.57`, but
the chain reports `9.570000`. Or - this is the real-world one - your price is
`19.89`, and the transfer comes in as `19.889` (the buyer's wallet or an
exchange rounded/truncated it).

Matching with exact equality would miss payments that should be accepted.
CleanTrade handles this with two rules (this is straight from the production
code):

```python
MATCH_TOLERANCE = 0.005          # half a cent step
match = (abs(amount - expected) < MATCH_TOLERANCE
         and round(amount, 2) == round(expected, 2))
```

`19.889` and `19.89` match. `19.88` does **not** match `19.89`. Tiny rounding
is forgiven, a real underpayment is not.

## Why the amount should be generated server-side

If you always use round numbers like `10.00`, two buyers sending the same
amount become ambiguous. CleanTrade generates the fractional part for you
(e.g. base `9` + `0.50..0.99` → `9.50`-`9.99`), and the API refuses to reuse
an amount that's already attached to an active order (HTTP 409). That's how
collisions are prevented without a smart contract.

## Detection speed

A poller checks incoming transfers on a short interval (~30 seconds in
CleanTrade). When it finds the match, the order flips to `paid` and a webhook
fires with the full `tx_id`. No need for your bot to poll - the webhook is
the delivery mechanism:

```json
{
  "order_id": "ord_8f3a...",
  "product_id": "indicator.zip",
  "amount": 19.889,
  "status": "paid",
  "tx_id": "70e07357b23cd5a68bdf83746a74459dd6a7cb62b92254ff73ff065046b9c773",
  "paid_at": "2026-08-23T10:01:00Z"
}
```

## What this gets you (and what it costs)

- ✅ No smart contracts, no gas fees per payment
- ✅ No custody - money goes straight to your wallet
- ✅ No KYC for your buyers
- ✅ Works with any TRC20-compatible wallet or exchange
- ✅ ~30 second detection, webhooks with up to 5 retries
- ⚠️ The buyer must send the **exact** amount - that's the one rule

The last point is why good UI matters: show the amount with enough decimals
and a copy button, and warn about "send the exact amount". A one-line message
prevents almost all support tickets.

## Try it

This pattern is implemented in the open-source SDK, including a ready
Telegram bot and a webhook receiver:

[github.com/smile-sk/cleantrade-python](https://github.com/smile-sk/cleantrade-python)

Docs and live API: [pay.cleantrade.info](https://pay.cleantrade.info/)
