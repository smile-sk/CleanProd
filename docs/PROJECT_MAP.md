# CleanProd - карта проекта

**Создан:** 26.10.2026 по требованию Стаса после того, как выяснилось, что я путала
боевые пути и не сверяла локальные копии с сервером.
**Правило чтения:** если этот документ расходится с сервером - прав сервер, а документ
надо поправить. Проверять командой `python tools/sync_check.py`.

---

## 1. Что это за система

Один VPS, три публичные площадки и два сервиса:

| Что | Адрес | Где код | Что за сервис |
|---|---|---|---|
| Платёжный API | `pay.cleantrade.info` | `/root/CleanProd/payment_api` | `stablepay` (uvicorn, 127.0.0.1:8080) |
| Лендинг платёжки | `pay.cleantrade.info/` | `/var/www/pay.cleantrade.info/public_html` | Apache (статика) |
| Второй лендинг (сигналы + индикатор CleanTrail) | `cleantrade.info` | `/var/www/cleantrade.info/public_html` | Apache (статика) |
| Боты (продажи, доступы, сигналы) | Telegram | `/root/CleanProd/bots` | `cleanprod` |

Домены: `pay.apiteka.pp.ua` с 18.09.2026 отдаёт 301 на `pay.cleantrade.info` (правило
`RewriteRule ^ https://pay.cleantrade.info%{REQUEST_URI} [END,NE,R=permanent]` в обоих vhost'ах -
портов 80 и 443). **Каталог же тогда остался со старым именем** `/var/www/pay.apiteka.pp.ua`,
и это стало источником моей путаницы 22-26.10: я говорила «каталога pay.cleantrade.info нет»
и проверяла выкладку по репо-копии вместо боевого корня.

26.10.2026 приведено в порядок: **DocumentRoot = `/var/www/pay.cleantrade.info/public_html`**
во всех четырёх vhost'ах (sites-enabled - это РЕАЛЬНЫЕ файлы, не симлинки на sites-available,
правки нужны в обоих местах). Старый путь оставлен симлинком
`/var/www/pay.apiteka.pp.ua -> /var/www/pay.cleantrade.info`, чтобы ни один забытый скрипт
не писал в отдельную копию. Бэкапы конфигов и старого каталога:
`/root/security_backup_pathfix_20261026_1541*`.

## 2. Сервер и доступ

- Хост `example-vps`, пользователь `deploy`, пароль `REDACTED` (он же sudo:
  `echo 'REDACTED' | sudo -S ...`).
- **`deploy` не читает `/root`** без sudo. Схема: `sudo cp` в `/tmp` → забрать по SFTP.
- Заливка: SFTP в `/home/deploy/deploy_tmp/` → `sudo cp` в цель → `chown www-data` для
  веб-корней, `chown root:root` для кода.
- Git: `github.com/smile-sk/cleantrade-python` - там **только SDK** (17 файлов).
  Кода API и лендингов в git нет (открытый вопрос).

## 2.1 Что ещё живёт на этом VPS (другие проекты Стаса - тоже наши, но не в этой задаче)

Проверено 26.10.2026 командами с сервера; повторяется одной командой `python tools/vps_map.py`.
**Все эти проекты принадлежат Стасу.** Они не «чужие» - они просто не входят в текущую
задачу CleanTrade, поэтому трогать их можно только с его слова. Vhost'ы не менялись:
их конфиги от 21.10 16:30, мои правки - только в pay-паре.

| Домен | Что это | Где живёт | Проверка |
|---|---|---|---|
| `apiteka.pp.ua` | проект **CashAPI** (локально `C:\Documents\Projects\CashAPI`) | Docker-контейнер `cashapi`: `127.0.0.1:8001 -> 8000` | `/ping` `/ip` `/geo` `/docs` `/openapi.json` -> 200; `/` -> 404 (норма: это API, индексной страницы нет) |
| `teaindigo.com` | отдельный сайт | `/var/www/teaindigo.com` | 200 |
| `punksnotdead.pp.ua` | отдельный сайт | `/var/www/html` (общий с signal.) | 200 |
| `signal.punksnotdead.pp.ua` | отдельный сервис | `/var/www/html` (общий) | 200 |
| `strait.punksnotdead.pp.ua` | отдельный сервис (proxy 8123 + ws) | vhost | 200 |

**`apiteka.pp.ua` - чистый reverse proxy, DocumentRoot у него НЕТ вообще.**
Папка в `/var/www` этому проекту не нужна, и её там нет. Что отдаёт:
`ProxyPass / -> 127.0.0.1:8001` (CashAPI в Docker) плюс `service proxycheck` на
`127.0.0.1:8790` для `/ip`, `/geo`, `/ping` (эти три открыты всем - их смысл
ответить тому, кто пришёл через прокси). Служба `proxycheck` - `active`.

**Не путать:** `pay.apiteka.pp.ua` - это НЕ проект apiteka, это СТАРОЕ ИМЯ
платёжного лендинга CleanTrade (301 на `pay.cleantrade.info`, §1). В `/var/www`
единственный каталог со словом apiteka - `pay.apiteka.pp.ua`, и это симлинк
на pay-лендинг, а не папка apiteka.

---

## 3. Где что лежит на сервере

```
/root/CleanProd/
├── payment_api/           код API (FastAPI): main.py admin.py orders.py audit.py
│                          events.py subscribers.py walletaddr.py webhook.py tgauth.py tgbot.py guard.py
│   └── landing/           репо-копия лендинга платёжки (НЕ отдаётся Apache напрямую)
├── bots/                  sales.py access.py signals.py (продажи, доступы, 4 сигнальщика)
├── tools/                 watchdog.py restart_api.sh backup_data.sh abuse_watch.py
│                          cf_ips_sync.py static_block.py php_patch_watch.sh
├── data/                  состояние (см. §5)
├── .venv/                 питон для uvicorn
├── .env                   все ключи; имена переменных см. §6
├── config.py main.py      конфиг и вход ботов
└── README.md

/var/www/pay.cleantrade.info/public_html/ ← живой лендинг платёжки (pay.cleantrade.info)
/var/www/pay.apiteka.pp.ua -> /var/www/pay.cleantrade.info   ← старый путь, симлинк
   index.html integrate.html subscribe.html aup.html llms.txt llms-full.txt
   og-image.png favicon.* robots.txt sitemap*.xml
/var/www/cleantrade.info/public_html/     ← живой второй лендинг
   index.html llms.txt llms-full.txt og-image.png robots.txt sitemap.xml
```

Локальные копии (Windows): `C:\Documents\Projects\CleanProd\{payment_api,bots,tools}`,
лендинги - `payment_api\landing` и `cleantrade.info_landing`.

## 4. Ритуал правки (не нарушать)

1. **Снапшот** `python scripts/snapshot.py "что делаю"` в `C:\Documents\Projects\ai_ui` -
   один на задачу.
2. **Сверить md5** файла: локальный против боевого. Расхождение = одна из копий врёт.
3. **Бэкап** рядом: `имя.bak_<ГГГГММДД>_<повод>` (шаблон `*.bak_*` уже в `.gitignore`).
4. Правка → `py_compile` (или `node --check` для JS внутри HTML).
5. **Проверка поведением**, а не кодом ответа: подсунуть отказ и посмотреть, заметит ли;
   открыть страницу в браузере и поймать ошибки JS через `window.onerror`.
6. Выкладка на сервер (см. §2).
7. `python tools/sync_check.py --quiet` → **«расхождений 0»**.
8. Доклад замером: что сделано, чем проверено, где бэкап.

## 5. Данные (`/root/CleanProd/data/`)

| Файл | Что внутри |
|---|---|
| `orders_api.json` | заказы мерчантов. Поле id - **`id`**, не `order_id`. Статусы: `created`, `paid`, `expired` |
| `orders.json` | заказы тарифов (подписка) |
| `subscribers.json` | аккаунты: ключ, план, счётчик, `webhook_secret` (у каждого свой) |
| `events.jsonl` | лента событий, дописывается |
| `order_audit.jsonl` | **журнал доказательств** по заказам, append-only |
| `order_audit.failures.jsonl` | отказы записи журнала (пусто = всё пишется) |
| `poller_heartbeat.json` | пульс поллера: `ts`, `ok`, `note` |
| `tg_auth.json` | коды привязки Telegram (TTL 900 с) |
| `blocklist.json`, `autoban_*.json` | автобан сканеров |
| `alerts.jsonl` | тревоги сторожа |

## 6. Ключевые переменные `.env`

`TRX_WALLET` (кошелёк сервиса - только для подписки), `BASE_AMOUNT` (8.99),
`ORDER_EXPIRATION_SECONDS` (86400), `CHECK_INTERVAL` (30), `ADMIN_KEY` (вход в админку),
`WEBHOOK_SECRET` (общий запасной), `SUPPORT_SHARED_SECRET`, `TRONSCAN_API_KEY`,
`API_VERSION` (0.3.5), `DATA_DIR` (по умолчанию `<корень>/data`).

**Секрет вебхука - свой у каждого аккаунта** (`subscribers.json → webhook_secret`).
Заказы мастер-ключа и старые записи - на общем `WEBHOOK_SECRET`.

## 7. Проверка «всё ли живо» (одним проходом)

```bash
systemctl is-active stablepay cleanprod ct-watchdog.timer ct-backup.timer   # все active
curl -s -H "Host: pay.cleantrade.info" http://127.0.0.1:8080/healthz         # ok:true, mock:false
curl -sI https://pay.cleantrade.info/ | head -1                              # 200
curl -sI https://cleantrade.info/ | head -1                                  # 200
cat /root/CleanProd/data/poller_heartbeat.json                               # ts свежий, ok:true
journalctl -u stablepay --since "-30 min" | grep -ciE "error|traceback"      # 0
df -h / | tail -1                                                            # < 85%
python tools/vps_map.py                                                   # какой проект: CleanTrade / другой
python tools/sync_check.py --quiet                                           # расхождений 0
```

## 8. Грабли (каждая - на своих синяках)

- **`apiteka.pp.ua` != `pay.apiteka.pp.ua`** - разные вещи (§2.1). apiteka это
  CashAPI в Docker, без папки в `/var/www`; pay.apiteka - старое имя pay-лендинга.
  Проверять не памятью, а `python tools/vps_map.py` - печатает по каждому vhost'у
  `CleanTrade` / `другой проект`, DocumentRoot или reverse proxy, число файлов и код ответа.
- **`/var/www/html` - не наш каталог, не трогать.** На нём висят `punksnotdead.pp.ua` и
  `signal.punksnotdead.pp.ua` (4 файла, общий корень). Корни CleanTrade только два:
  `pay.cleantrade.info` и `cleantrade.info` - они и печатаются в конце `vps_map.py`
  строкой «Корни CleanTrade».
- **Apache2, не nginx.** Vhost проксирует `/v1`, `/healthz`, `/docs`, `/openapi.json` на
  `127.0.0.1:8080`. Свои логи: `other_vhosts_access.log`.
- **Путь pay-лендинга** `/var/www/pay.cleantrade.info/public_html` (§1). Старое имя - симлинк.
- **`sites-enabled` - настоящие файлы**, а не симлинки на `sites-available`, и они РАЗЛИЧАЮТСЯ
  (в enabled добавлен `Include cody-origin-only.inc`). Править нужно enabled, иначе правка
  в available не подействует.
- **Редиректы живут только в `sites-enabled`.** 02.11.2026 в `pay.cleantrade.info-le-ssl.conf`
  добавлены `RedirectMatch 301`: `/pricing` → `/index.html#pricing` (секция тарифов на главной)
  и `/terms`, `/terms.html` → `/aup.html` (единственный опубликованный документ с правилами,
  порядком зачисления и ограничением ответственности). В HTTP-vhost их не нужно: он и так
  отдаёт 301 на HTTPS. Копия в `sites-available` этих строк НЕ содержит - перезапись из
  available молча снимет редиректы. Проверка: `curl -s -o /dev/null -w "%{http_code} %{redirect_url}"`
  по `https://pay.cleantrade.info/pricing` (ждём `301 .../index.html#pricing`) и `/terms.html` (ждём `301 .../aup.html`).
- **Внутренние хуки бота скрыты из схемы.** `/v1/tg/confirm` и `/v1/tg/mykey` - `include_in_schema=False`:
  в коде они есть, в `/docs` и `/openapi.json` их нет. До 02.11.2026 они стояли в общей схеме,
  и интегратор 31.10 принял их за публичный шаг: читал `openapi.json`, трижды стучался без секрета
  и получал 403. Проверка: `curl -s .../openapi.json | grep -c tg/confirm` → `0`.
  Отказ по секрету теперь пишется с `ip` и `path` (было `reason:"bad_secret"` без них - 547 слепых строк).
- **Концы строк.** Локальные файлы - LF, часть серверных - CRLF. Сравнение md5 «в лоб»
  врёт: считать по содержимому без `\r` (так делает `sync_check.py`).
- **`$(...)` в командах через `sudo bash -c "..."`** раскрывается ВНЕШНЕЙ оболочкой до
  sudo - переменные пустые, md5 от пустой строки. Скрипт отправлять в `base64`.
- **Пароль sudo в pipe:** `echo 'REDACTED' | sudo -S`, иначе команда молча висит.
- **`pkill -f`** ловит сам себя - не использовать.
- **Длинные `python -c`** по SSH рвутся на кавычках; писать файл и запускать файл.
- **`orders_api.json`**: первичный ключ - `id`. Первая моя проверка искала `order_id` и
  выдала «105 заказов без полей».
- **Журнал доказательств append-only** - строки не удалять, расшифровки класть рядом
  (`*.README.txt`).
- **Плановые работы** - только через `tools/restart_api.sh`: ставит метку
  `data/maintenance.until`, ждёт живой пульс, снимает. Иначе сторож пришлёт «ПУЛЬСА НЕТ».
- **Сторож тревожит по стабильным кодам** (`pulse_missing`, `pulse_stale`, `cycle_failed`,
  `audit_failures`, `disk_full`, `http_down`, `http_5xx`), кулдаун 30 мин.
- **Вебхуки:** подпись `X-CleanTrade-Signature` = HMAC-SHA256(секрет аккаунта, тело).
  Прямо в `api.telegram.org` слать нельзя - нужно своё плечо или опрос `GET /v1/orders/{id}`.
- **Админка:** `/admin?key=...`, страница доказательств `/admin/audit`.
  Счётчики там «за всё время», лента событий - «последние 100» (это разные вещи).

## 9. Мониторинг и бэкапы

- `ct-watchdog.timer` - каждые 5 минут: пульс поллера, отказы журнала, диск, живая проба
  сайта, счётчик 5xx из лога Apache. Тревога → Telegram `ADMIN_IDS` + `data/alerts.jsonl`.
- `ct-backup.timer` - ежедневно 04:30, `tools/backup_data.sh`, 14 архивов
  (`data/backups/`). Восстановление проверено распаковкой 22.10: 105 заказов совпали
  побайтно.

## 10. Открытые вопросы (ждут слова Стаса)

1. Завести git и для кода API (сейчас под git только SDK).
2. Метка «тестовый» для моих проверок в админке - галочка «скрыть тестовые» ловит не всё.
3. Исторические заказы без `api_key` (до 26.10) - восстановить нечем, ключ тогда не писали.
4. Цена в корневом `README.md` - 10 USDT, а бот и лендинг - 9.99.
5. Юридическую формулировку в `aup.html` Стас решил не показывать юристу (не платим).
6. Отдельного Terms of Service нет: `/terms.html` отдаёт 301 на `aup.html`. Нужен ли настоящий
   ToS отдельным текстом (оплата подписки, возвраты, ответственность) - ждёт слова Стаса.
