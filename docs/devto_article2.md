# Why flat-rate pricing beats percentage-based crypto gateways for micro-SaaS

If you sell digital goods and your payment processor takes a cut of every
payment, you're not just paying a fee - you're paying a **tax on your own
growth**. The more your micro-SaaS earns, the more the gateway earns from you.

Classic crypto gateways charge **0.5% (NOWPayments, Plisio) to 1%+ (Coinbase Commerce, CoinGate, BitPay)** per transaction. That sounds
small. Let's do the math that actually matters.

## The math

Say a gateway takes 0.5% per payment. Here's what that costs you at different
monthly volumes, next to a flat $29.99 subscription (CleanTrade Pro):

| Monthly volume | 0.5% gateway fee | CleanTrade flat | You keep extra |
| --- | --- | --- | --- |
| $1,000 | $5 | $29.99 | percentage wins by $24.99 |
| $5,000 | $25 | $29.99 | **+$4.99** |
| $10,000 | $50 | $29.99 | **+$20.01** |
| $50,000 | $250 | $29.99 | **+$220.01** |

At $10k/month that's $240 a year back in your pocket. At $50k it's
**$2,640 a year**. Not a fortune - which is why the real argument is custody
servers, or simply profit.

## Why percentage fees feel harmless but aren't

1. **They scale against you.** Percentage fees grow with your success. A flat
   fee stays flat. Your cost structure should be predictable, not a gamble on
   your own growth.
2. **They stack with everything else.** Card processors, FX spread on
   crypto-to-fiat, withdrawal fees - the "2%" is rarely the only cost.
3. **They punish high-ticket items.** Sell a $99 product? 3% is $3. Sell a
   $499 course? That's $15 per sale just in processor fees.

Flat pricing means: **you know your costs, forever.** $29.99/month, 0% per
payment, no surprise deductions.

## What you actually get with CleanTrade

- **0% per transaction** - flat subscription, nothing else
- **Non-custodial** - payments go straight to *your* TRC20 wallet, we never
  hold your funds
- **No KYC, no approval queue** - API key in about a minute
- **Webhooks with retries** - `tx_id` payload so you can deliver goods
  automatically
- **~30 second detection** via unique-amount matching - no smart contracts,
  no extra gas fees

Plans (all include the 0% fee model):

| Plan | Monthly | Yearly (12 months for the price of 10) | Orders/mo |
| --- | --- | --- | --- |
| Free | $0 | - | 5 |
| Starter | $9.99 | $99.90 | 100 |
| Pro | $29.99 | $299.90 | 3,000 |
| Business | $79.99 | $799.90 | Unlimited |

## The one case where percentage wins

If you process under ~$6,000/month, a 0.5% gateway is cheaper than $29.99 flat.
That's exactly why there's a **Free tier** - 5 orders a month, no card
required, so you can grow into flat pricing on the same integration.

## Bottom line

Percentage pricing is a bet against your own growth. Flat pricing is a
commitment to it. When your micro-SaaS starts earning, you want the fee that
doesn't grow with you - and a gateway that never touches your money.

The SDK is open source:
[github.com/smile-sk/cleantrade-python](https://github.com/smile-sk/cleantrade-python)

Try the calculator on the landing page - it does this math for you:
[pay.cleantrade.info](https://pay.cleantrade.info/)
