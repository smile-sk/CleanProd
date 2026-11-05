# Flat-fee vs percentage crypto gateways: honest math for micro-SaaS

If your micro-SaaS or Telegram bot takes crypto payments, the fee structure of your gateway quietly decides how much revenue you actually keep. Most processors charge a **percentage of every payment**. A few charge a **flat monthly fee**. Which one is really cheaper? The answer is uncomfortable - and it depends entirely on your volume. So let's do the math honestly, including the point where the percentage gateway wins.

## The honest math

The top percentage gateways charge **0.5% per transaction** (NOWPayments, Plisio); Coinbase Commerce, CoinGate and BitPay sit at **1% or more**. It sounds small. Here's what 0.5% actually costs at different monthly volumes, next to CleanTrade's flat $29.99 Pro plan:

| Monthly volume | 0.5% gateway fee | Flat $29.99 | Winner |
| --- | --- | --- | --- |
| $1,000 | $5 | $29.99 | percentage by $24.99 |
| $3,000 | $15 | $29.99 | percentage by $14.99 |
| $6,000 | $30 | $29.99 | ~break-even |
| $10,000 | $50 | $29.99 | flat by $20.01 |
| $50,000 | $250 | $29.99 | flat by $220.01 |

On the cheaper **$9.99 Starter** plan the break-even drops to roughly **$2,000/month**. Below that, a percentage gateway is genuinely cheaper - and any honest flat-rate provider should say so out loud.

## Why percentage fees still sting

1. **They scale against you.** The more your micro-SaaS earns, the more the gateway earns from you. A flat fee stays flat. Your cost structure should be predictable, not a bet against your own growth.
2. **They stack with everything else.** Network fees, FX spread on crypto-to-fiat, withdrawal fees - the "0.5%" is rarely the only cost.
3. **They punish high-ticket items.** Sell a $499 course at 1%? That's ~$5 per sale. At volume, it adds up.

## The real reason to switch isn't the fee - it's custody and DX

Flat vs percentage is a real question, but it's the **second** question. The first: **where does the money live?**

- **Custodial gateways** hold your funds on their balance until you withdraw - and can freeze or delay payouts.
- **Non-custodial** means the payment lands directly on *your own* wallet from the first second.

CleanTrade is non-custodial and flat: no KYC, no approval queue, API key in about a minute, webhooks with retries, ~30-second detection via unique-amount matching (no smart contracts, no gas fees).

## Three lines of code

```python
from cleantrade import Client

client = Client("sp_live_YOUR_KEY")                  # API key in ~a minute, no KYC
order = client.create_order(product_id="course.zip") # unique amount, e.g. 19.57
# show order.amount + order.wallet to the buyer; webhook fires with tx_id when paid
```

Real SDK, open source: [github.com/smile-sk/cleantrade-python](https://github.com/smile-sk/cleantrade-python)

## Plans

| Plan | Monthly | Yearly (12 months for the price of 10) | Orders/mo |
| --- | --- | --- | --- |
| Free | $0 | - | 5 |
| Starter | $9.99 | $99.90 | 100 |
| Pro | $29.99 | $299.90 | 3,000 |
| Business | $79.99 | $799.90 | Unlimited |

## When percentage wins (honesty, again)

If you process under ~$2,000/month, a 0.5% gateway is cheaper than any flat plan. That's exactly why CleanTrade has a **Free tier** - start free, grow into flat pricing on the same integration, without switching gateways twice.

## Bottom line

Flat pricing isn't a magic 10x saving - at realistic competitor rates it wins by a margin, not an order of magnitude. The bet worth making is **self-custody and DX**: a gateway that never touches your money and takes three lines of code to integrate.

Try the calculator - it does this math for your numbers: [pay.cleantrade.info](https://pay.cleantrade.info/)

---

*Tags: crypto, payments, api, python, webdev*
