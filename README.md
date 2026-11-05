# CleanProd

Non-custodial USDT payment gateway on TRON, in production.

The customer sends their own TRC-20 wallet with each order. Buyers pay directly to that
wallet; the gateway watches the chain and confirms the payment. No funds are ever held by
the service, and the amount uniqueness check is scoped per wallet.

## Parts

| Part | What it is |
|---|---|
| `payment_api/` | FastAPI service: orders, payments, subscriptions, Telegram auth, webhooks |
| `bots/` | Telegram sales and support bots |
| `core/` | Payment logic, auth, wallet handling |
| `tools/` | Cloudflare IP sync, abuse watch, static blocking |
| `deploy/` | systemd units and nginx config |
| `cleantrade.info_landing/` | Merchant landing page |
| `docs/` | Integration guide, pricing, roadmap, marketing |

## Secrets

Everything sensitive lives in `.env` (gitignored). A template is in `.env.example`.
Deployment credentials are never stored in code.
