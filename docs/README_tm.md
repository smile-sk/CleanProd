# CleanProd

Единая система: продажа CleanTrail (индикатор на Pine Script + PDF-гайды) за **10 USDT (TRC20)**
через Telegram-ботов + 4 сигнальщика для покупателей. Один процесс, один сервис.
Продажи идут через **собственный CleanTrade Payment API** (собака ест свой корм) -
бот является первым живым клиентом продукта.

## Архитектура

```
CleanProd/
├── main.py          # ЕДИНАЯ точка входа: 2 бота + вебхук-приёмник + 4 сигнальщика
├── config.py        # .env + пути + настройки
├── core/            # переиспользуемое ядро
│   ├── payment.py   #   детектор USDT: уникальная сумма -> совпадение (отдаёт tx-id)
│   ├── auth.py      #   токены доступа + авторизация + blocked_users
│   └── log.py       #   логи
├── bots/
│   ├── sales.py     # @CleanTrade_bot - продажи: /buy -> заказ в API -> вебхук -> выдача
│   ├── access.py    # @HexusCrypto_bot - гейт доступа по токену
│   └── signals.py   # 4 сигнальщика (impulse, FVG, ликвидации, сессии)
├── payment_api/     # CleanTrade Payment API (продукт, отдельный сервис)
├── assets/          # CleanTrail.zip + media/ (скриншоты витрины)
├── data/            # состояние (gitignored): orders, tokens, authorized/blocked_users
└── deploy/          # systemd-юниты (cleanprod, stablepay) + deploy.sh
```

## Как работает продажа (конвейер)

1. Покупатель жмёт `/buy` → бот создаёт заказ в API: `POST /v1/orders` на `127.0.0.1:8080`.
2. API генерит **уникальную сумму** `9.5x` USDT (цена 10, `BASE_AMOUNT=9`) и отдаёт кошелёк;
   бот показывает покупателю: «переведи ровно N USDT на адрес TRC20...».
3. **Единственный поллер TronScan живёт в API** (`stablepay`), каждые 30 сек ловит совпадение суммы.
4. API шлёт вебхук на локальный приёмник бота (`127.0.0.1:8081/webhook/cleanprod`, aiohttp) →
   бот выдаёт CleanTrail.zip + токен доступа и уведомляет админа.
5. `/status` тоже спрашивает API. Доступ - по токену через @HexusCrypto_bot.

Покупатели, заблокировавшие бота, попадают в `data/blocked_users.txt`:
рассылка им пропускается (лог не шумит Forbidden), **доступ сохраняется**;
любое сообщение от юзера снимает флаг автоматически.

## Запуск

```bash
pip install -r requirements.txt
cp .env.example .env   # вписать секреты
python main.py         # всё сразу: 2 бота + вебхук-приёмник + 4 сигнальщика
```

Тест платёжки без реальных денег: `USE_MOCK=1` в `.env`.

## Деплой на VPS

```bash
bash deploy/deploy.sh
```

Скрипт ставит venv и два systemd-юнита:
- `cleanprod` - ботферма (`python main.py`), включая вебхук-приёмник на 8081;
- `stablepay` - CleanTrade Payment API (`uvicorn payment_api.main:app`) на `127.0.0.1:8080`.

На VPS apache2 проксирует `https://pay.cleantrade.info` → `127.0.0.1:8080`
(SSL: `certbot --apache`, автопродление). Логи: `journalctl -u cleanprod -f`.

## Статус

- Фаза 1: рефакторинг ботофермы в один процесс - ✅
- Фаза 2: CleanTrail продаётся через свой API (E2E проверено вживую) - ✅
- Фаза 3: продукт наружу (тарифы $9.99/$29.99/$79.99, каналы сбыта, доки) - впереди
