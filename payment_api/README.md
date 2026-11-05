# CleanTrade Payment API

Приём USDT (TRC20) по **уникальной сумме**: продавец создаёт заказ со своим
кошельком, покупатель переводит ровно `9.5x` USDT на этот кошелёк, поллер ловит совпадение,
статус становится `paid`, на `webhook_url` продавца уходит уведомление.

**Статус: фаза 3.2 выполнена - биллинг работает.
CleanTrail продаётся через API (фаза 2), подписки продаются через API (фаза 3.2).**

## Документы

* `PRICING.md` - тарифы и модель продаж (Free $0 / Starter $9.99 / Pro $29.99 / Business $79.99; годовые - 12 мес по цене 10).
* `ROADMAP.md` - статус фаз и план.

## Эндпоинты

| Метод | Путь                | Назначение                                     |
|-------|---------------------|------------------------------------------------|
| GET   | `/healthz`          | живость сервиса                                |
| POST  | `/v1/orders`        | создать заказ: кошелёк продавца + уникальная сумма |
| GET   | `/v1/orders/{id}`   | статус заказа (заказы тарифов - по order_id без ключа) |
| POST  | `/v1/subscribe`     | публичная подписка на тариф (без ключа)        |
| GET   | `/v1/subscribers/me`| план/лимит/использовано по ключу               |

Аутентификация: заголовок `X-API-Key`. Два вида ключей:
* мастер-ключ из `.env` (`PAYMENT_API_KEY`) - без лимитов (это мы);
* ключ подписчика `sp_live_...` (выдаётся через `/v1/subscribe`) -
  лимит по тарифу, при исчерпании **402 Payment Required**.

### Создание заказа
```bash
curl -X POST https://pay.cleantrade.info/v1/orders \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"product_id": "CleanTrail", "webhook_url": "https://mydomain.ru/hook"}'
```
Ответ:
```json
{"order_id":"abc123def456","product_id":"CleanTrail","amount":9.57,
 "wallet":"T...","status":"created","created_at":"2026-08-19T22:00:00+00:00"}
```
Покупатель переводит ровно **9.57 USDT** (TRC20) на адрес `T...`.

### Статус заказа
```bash
curl https://pay.cleantrade.info/v1/orders/abc123def456 -H "X-API-Key: $KEY"
```

### Подписка на тариф (публично, без ключа)
```bash
curl -X POST https://pay.cleantrade.info/v1/subscribe \
  -H "Content-Type: application/json" \
  -d '{"plan": "starter", "customer_ref": "@mytg", "webhook_url": "https://mydomain.ru/hook"}'
```
После оплаты поллер активирует план, в вебхук и в `GET /v1/orders/{id}`
попадёт `api_key` подписчика. Стр. подписки: `/subscribe.html`.

### Вебхук при оплате (POST на webhook_url)
```json
{"order_id":"abc123def456","product_id":"CleanTrail","amount":9.57,
 "status":"paid","tx_id":"...","paid_at":"2026-08-19T22:05:00+00:00"}
```
До 5 попыток с ретраями. Ответ 2xx = доставлено.

## Жизненный цикл заказа

`created` → (оплата) → `paid` | `expired` (после 24 ч).

## Запуск (dev)

```bash
pip install -r requirements.txt
uvicorn payment_api.main:app --port 8080
```

`USE_MOCK=1` в `.env` - тест без реальных денег.

## Деплой на VPS

1. Папка проекта в `/root/CleanProd` (вместе с ботофермой).
2. `.env` из `.env.example` (секреты: ключ API, кошелёк, TronScan API key).
3. `bash deploy/deploy.sh` - venv + systemd-юниты (`stablepay`, `cleanprod`).
4. DNS: A-запись `pay.cleantrade.info` → IP VPS (сделано);
   SSL: `certbot --apache -d pay.cleantrade.info` (сделано, автопродление);
   старый `pay.apiteka.pp.ua` - 301-редирект на новый.

Публичный адрес: `https://pay.cleantrade.info`.


## Админ-панель

Панель мониторинга в стиле лендинга (тёмная тема), доступ по ключу `ADMIN_KEY` из `.env`:

- `GET /admin?key=<ADMIN_KEY>` - страница (или заголовок `X-Admin-Key`)
- `GET /admin/api/overview` - сводка: заказы (всего/paid/created/expired),
  подписчики, вебхуки ok/fail, ошибки поллера, отказы авторизации, аптайм
- `GET /admin/api/orders` - последние заказы
- `GET /admin/api/subscribers` - подписчики
- `GET /admin/api/events` - лента событий (data/events.jsonl, переживает рестарт)

События: order_created, order_paid, order_expired, plan_activated,
webhook_ok, webhook_fail, auth_fail, limit_reached, poller_error.
Автообновление страницы - каждые 15 сек.
