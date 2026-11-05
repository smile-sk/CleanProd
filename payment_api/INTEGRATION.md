# Integration Guide - CleanTrade Payment API

Принимай USDT (TRC20) в своём боте или на сайте за 5 минут. Все примеры ниже - рабочие, копируй как есть.

Базовый URL: `https://pay.cleantrade.info`

---

## Как это работает - 4 шага

1. **Получаешь API-ключ** - подписка на странице тарифов, получаешь ключ (free сразу, платный - в течение минуты после того, как мы найдём оплату) вида `sp_live_...`. Он идентифицирует тебя и считает лимит плана.
2. **Создаёшь заказ** - `POST /v1/orders` со своим TRC20-кошельком. В ответ: уникальная сумма (9.57, 9.84…) для этого заказа, а в поле `wallet` - тот же адрес, что ты передал.
3. **Покупатель платит** - показываешь ему сумму и свой адрес. Он шлёт USDT (TRC20) - поллер видит перевод на этом кошельке и, если сумма совпала и перевод пришёл после создания заказа, ставит `paid`.

> **`wallet` обязателен в каждом заказе.** Это твой TRC20-адрес: 34 символа, начинается с `T`, проверяется по контрольной сумме. Мусор, обрезанный адрес или опечатка - `422`, заказ не создаётся.
> Сумма уникальна **в пределах этого кошелька**: 9.76 на двух разных кошельках - норма, 9.76 дважды на одном кошельке - `409`.
> Заказ ждёт оплату 24 часа, после чего сумма освобождается и может достаться новому заказу. Если покупатель заплатил позже - деньги уже на твоём кошельке, разбираешься с ним сам: мы этот платёж не ищем.
4. **Вебхук → выдача** - мы ловим оплату за ~30 сек и шлём вебхук с `tx_id`. Твой код выдаёт ключ, файл или доступ.

---

## Python (requests)

```python
# pip install requests
import requests, time

API = "https://pay.cleantrade.info"
KEY = "sp_live_YOUR_KEY"

# 1. create an order
r = requests.post(f"{API}/v1/orders",
    json={"product_id": "indicator.zip",
          "wallet": "TRX_ADDRESS",  # твой TRC20-кошелёк
          "customer_ref": "user123",
          "webhook_url": "https://my.site/hook"},
    headers={"X-API-Key": KEY})
order = r.json()
print("Show buyer:", order["amount"], "USDT to", order["wallet"])

# 2. wait for payment (or just wait for the webhook)
while True:
    st = requests.get(f"{API}/v1/orders/{order['order_id']}",
                      headers={"X-API-Key": KEY}).json()
    if st["status"] == "paid":
        print("Paid!", st["tx_id"])
        # deliver the goods here
        break
    time.sleep(5)
```

## cURL

```bash
# 1. create an order
curl -X POST https://pay.cleantrade.info/v1/orders \
  -H "X-API-Key: sp_live_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"product_id":"indicator.zip","wallet":"TRX_ADDRESS","customer_ref":"user123","webhook_url":"https://my.site/hook"}'
# → {"order_id":"abc123","amount":9.57,"wallet":"T...","status":"created"}

# 2. check status
curl https://pay.cleantrade.info/v1/orders/abc123 \
  -H "X-API-Key: sp_live_YOUR_KEY"
# → {"status":"paid","tx_id":"6b3a..."}
```

## JavaScript (для сайтов)

```javascript
const API = "https://pay.cleantrade.info";

// 1. create an order
const res = await fetch(API + "/v1/orders", {
  method: "POST",
  headers: { "X-API-Key": "sp_live_YOUR_KEY", "Content-Type": "application/json" },
  body: JSON.stringify({ product_id: "indicator.zip", wallet: "TRX_ADDRESS", webhook_url: "https://my.site/hook" })
});
const order = await res.json();
console.log("Show buyer:", order.amount, order.wallet);

// 2. poll until paid
const t = setInterval(async () => {
  const st = await (await fetch(API + `/v1/orders/${order.order_id}`,
    { headers: { "X-API-Key": "sp_live_YOUR_KEY" } })).json();
  if (st.status === "paid") { clearInterval(t); console.log("Paid!", st.tx_id); }
}, 5000);
```

---

## Вебхук-приёмник (автовыдача)

Самый простой путь: мы сами вызываем ТВОЙ URL, когда приходит оплата. Готовый приёмник на Python (aiohttp):

```python
# pip install aiohttp   → run: python webhook.py
from aiohttp import web

async def hook(request):
    data = await request.json()
    if data["status"] == "paid":
        print("ORDER PAID:", data["order_id"], data["tx_id"])
        # ← deliver the goods here (file, key, access)
    return web.Response(text="ok")

app = web.Application()
app.router.add_post("/hook", hook)
web.run_app(app, port=8081)
```

**Payload вебхука:** `{"order_id", "product_id", "amount", "wallet", "status", "tx_id", "paid_at", "customer_ref"}`.
Для заказов тарифов (`product_id` = `plan:<тариф>`) добавляются `plan` и `api_key` - ключ подписчика.
Повторяем до 5 раз, если твой сервер не ответил `200` (по одной попытке за проход поллера, раз в 30 с).

С 22.10.2026 у каждого аккаунта СВОЙ секрет подписи (один на сервис означал бы, что мерчант, знающий секрет, может подделать уведомление другому). Свой секрет отдаётся ключом API:

```
GET /v1/subscribers/me            -> {"webhook_secret": "…", …}
POST /v1/mykey {"tg_code": "…"}   -> {"webhook_secret": "…", …}
```

и приходит в Telegram вместе с ключом (команда `/key`). Храни его как пароль: он подписывает уведомления только твоего аккаунта. Засветился - напиши поддержке, выдадим новый; старый сразу перестаёт подходить, а ключ API при этом не меняется.

**Проверка подписи.** Каждая доставка подписана HMAC-SHA256 
от строки `<timestamp>.<тело>`; заголовки `X-CleanTrade-Timestamp` и `X-CleanTrade-Signature: sha256=...`.
Сравнивать нужно СЫРОЕ тело запроса (не перекодируй JSON!) и заодно 
проверять свежесть `timestamp` - это закрывает повтор чужой доставки:

```python
from cleantrade import verify_webhook

ok = verify_webhook(
    body=await request.body(),
    signature=request.headers.get("X-CleanTrade-Signature", ""),
    timestamp=request.headers.get("X-CleanTrade-Timestamp", ""),
    secret=os.environ["CLEANTRADE_WEBHOOK_SECRET"],
)
if not ok:
    return web.Response(status=400)
```

---

## FAQ

**Что если покупатель отправил другую сумму?**
Суммы сравниваются **до 2 знаков**: `19.889` засчитывается как `19.89`. USDT в TRC20 хранит 6 знаков, поэтому небольшой «хвост» (19.889 против 19.89) - это норма, платёж всё равно поймается. Если сумма действительно другая (например, 19.88 против 19.89) - совпадения нет и вебхука нет: заказ остаётся `created` и сгорает через 24 часа. Деньги при этом уже на твоём кошельке, так что закрывать эту разницу с покупателем - тебе; мы такой платёж не ищем и не сопоставляем.

**Как вернуть оплату?**
Платежи приходят сразу на **твой** кошелёк - мы их не держим, поэтому возврат делаешь ты сам: отправь USDT обратно тем же кошельком. Запрашивать что-то у нас не нужно и нечего.

**Почему только TRC20?**
USDT в сети Tron имеет самую низкую комиссию (~1$) и это стандарт для продажи ботов и индикаторов. Другие сети можно добавить позже.

**Мой API-ключ в безопасности?**
Держи его на сервере. Никогда не клади во фронтенд-код и не отдавай покупателю в браузер. Ключ один на аккаунт: выпуска нескольких ключей пока нет.

**Что если превышу лимит плана?**
API ответит `402 Payment Required` с подсказкой. Обнови тариф на странице подписки - ключ активируется в течение минуты после того, как мы найдём оплату.

**Есть ли скидка за год?**
Да: годовые тарифы (`starter_yearly`, `pro_yearly`, `business_yearly`) - 12 месяцев по цене 10 (Starter $99.90, Pro $299.90, Business $799.90). Крипта не карты, автопродления нет - это разовый платёж на 365 дней, ключ не теряется.

**Как связаться с поддержкой?**
Telegram: [@CleanTrade_bot](https://t.me/CleanTrade_bot) - обычно отвечаем в течение нескольких часов. Укажи `order_id` или API-ключ, и мы поможем.

## Free-тариф: активация мгновенная (без поллера)

Free стоит $0, платить нечего - поэтому `POST /v1/subscribe` с `plan:"free"` НЕ создаёт
ожидающий заказ, а сразу активирует подписку и возвращает ключ:

    POST /v1/subscribe  {"plan":"free","customer_ref":"you@example.com"}
    → 201 {"status":"paid","amount":0.0,"api_key":"sp_live_...","plan_expires_at":"..."}

Повторный запрос того же `customer_ref` возвращает ТОТ ЖЕ ключ (`"reused":true`), дублей нет.
Если у клиента уже активен платный тариф - free не выдаётся (409, понижения не делаем).

Анти-накрутка: `FREE_IP_DAILY_LIMIT` (по умолчанию 5) - сколько free-ключей в сутки можно
выдать с одного IP; счётчики в `data/free_ips.json`, значение 0 = без лимита. Превышение → 429.

Платные тарифы как раньше: заказ на точную сумму → поллер видит перевод → вебхук с ключом →
`GET /v1/orders/{id}`.

## Безопасность вебхуков (SSRF)

`webhook_url` задаёт пользователь, а POST делает НАШ сервер из внутренней сети. Поэтому адрес
проверяется дважды - при создании заказа и перед отправкой:

* только `http://` / `https://` (никаких `file://`, `gopher://` и прочих схем);
* хост обязан резолвиться, и ВСЕ его адреса должны быть публичными: loopback, приватные
  (10/172/192.168), link-local (x.x.x.x - метаданные облака), `::1`, multicast и
  reserved запрещены;
* редиректы не выполняются (`allow_redirects=False`) - иначе проверку обошёл бы переброс на 127.0.0.1.

Отказ → 400 `invalid webhook_url: ...` и событие `webhook_rejected`. Небезопасные адреса,
попавшие в базу раньше, отбрасываются и при отправке (`webhook_fail`), заказ не ломается.

## Аудит и доказательства

По каждому заказу ведётся журнал, который **только дописывается**: `data/order_audit.jsonl`.
В нём: момент создания заказа, ожидаемый кошелёк и сумма, каждая проверка блокчейна
(сколько раз, когда, какой канал ответил), найденный перевод (`tx_id`, сумма, время
блока), все попытки доставки вебхука и ответы твоего сервера.

Зачем это нужно. Претензия «я продал товар за $1000, а из-за вас не получил денег»
закрывается не словами, а записью: заказ был создан такого-то числа, ждали такую-то
сумму на такой-то кошелёк, проверяли N раз, перевода не было - и это любой человек
может проверить независимо, потому что блокчейн публичный.

Правило зачисления - одно и оно публичное:

1. входящий перевод USDT (TRC20) на кошелёк, указанный в заказе;
2. сумма равна сумме заказа (допуск 0.005);
3. время блока позже создания заказа;
4. `tx_id` ещё не зачтён другому заказу.

Нет любого пункта - заказ не зачтён и вебхук не отправлен. Перевод после истечения
срока не зачитывается: деньги уже на кошельке мерчанта, разбирается он с покупателем.

Записи по заказу человеку: `GET /admin/api/orders/{order_id}/audit?key=...` (JSON)
и страница `GET /admin/audit?key=...&order_id=...` - там же таблица по дням:
создано / оплачено / истекло / вебхуки / проверки.

---

## Админка

* таблицы заказов и подписчиков - с горизонтальным скроллом (широкие таблицы больше не обрезаются);
  в заказах видны «Клиент» и «Ключ», счётчик попыток вебхука показывает реальный максимум (5, не 3);
* `POST /admin/api/activate` `{"customer_ref":"...","plan":"free"}` - выдать тариф вручную
  (ключ существующего клиента сохраняется, срок продлевается с сегодня);
* у отказа авторизации (`auth_fail`) в журнале теперь есть `ip` клиента - свои пробы видно от чужих.

---

См. также: [Swagger docs](https://pay.cleantrade.info/docs) · [OpenAPI](https://pay.cleantrade.info/openapi.json) · [Тарифы](https://pay.cleantrade.info/subscribe.html)
