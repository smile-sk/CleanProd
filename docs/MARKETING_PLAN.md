# CleanTrade Payment API - план раскрутки v2 (западный рынок)

Старт: 2026-08-22. Переписан 24.08 после фактов v1. Цель: Tier-1 (США/ЕС/SEA),
аудитория - разработчики Telegram-ботов, микросервисов, микро-SaaS и крипто-мерчанты.

Правила игры: галочка = выполнено. Одна задача = одна галочка.

---

## КОРРЕКЦИЯ КУРСА v3 (25.08) - после внешней ревизии

Внешняя ревизия + перепроверка фактов поиском. Принято, потому что цифры
подтвердились. Ключевые сдвиги:

1. **Позиционирование: DX + self-custody + no-KYC, НЕ цена.** Лидеры рынка
   (NOWPayments, Plisio) берут 0.5%/tx, у NOWPayments уже есть non-custodial режим.
   Flat vs % - скромное, не драматичное преимущество: безубыток Starter ~$2k/мес,
   Pro ~$6k/мес при 0.5%. Ниже порога процентник дешевле, и наша же аудитория
   (HN/Dev.to) это мгновенно посчитает. Продаём «деньги сразу на твой кошелёк +
   3 строки кода», а не «мы дешевле».
2. **Прямые конкуренты, которых не было в A1:** @CryptoBot / Crypto Pay (бесплатный
   нативный провайдер внутри Telegram) и BTCPay Server (бесплатный self-host).
   Отвечаем лоб в лоб в FAQ и контенте: у @CryptoBot деньги оседают на балансе бота
   (не твой кошелёк); BTCPay - сам хостишь ноду, мы - managed API.
3. **Честный калькулятор:** 0.5-1% (не 2%), ведёт со Starter $9.99, вход - «продаж/мес
   × средний чек» вместо абстрактного «объём $», показывает безубыток и честно пишет,
   когда flat дороже.
4. **AUP + план абьюза:** aup.html готов. No-KYC притягивает вертикали, от которых
   отшивает Stripe - готовимся к абьюзу до, а не после.
5. **Оплата подписок = dogfood:** принимаем оплату своих подписок своим же шлюзом
   (USDT). Сторонний процессор не подходит - нервно относится к «фасаду для анонимных
   крипто-платежей».
6. **Перефокус недель 1-2:** B2B-директории (G2, Crunchbase, Software Advice) - долгий
   SEO-хвост (DR), а не текущие подписчики. Больше веса нишевым каналам: контент
   «self-custody vs @CryptoBot», Show HN, Indie Hackers, TG-каталоги ботов.

Вывод: продолжаем. Продукт рабочий, цена продолжения низкая (наше время), апсайд
реальный. Ставка не на уникальность, а на нишевую дисциплинированную SaaS с сильными
конкурентами. Выигрыш - DX и доверие.

---

## ДИАГНОЗ v1 - что было не так (факты 22-24.08)

| Канал | Результат | Причина | Решение v2 |
| --- | --- | --- | --- |
| Dev.to | ✅ статья №1 живёт | площадка работает | продолжаем |
| Medium | ✅ зеркало живёт + canonical | работает | продолжаем |
| Hashnode | ❌ AutoMod заархивировал | крипта на бесплатных hashnode.dev = спам | закрыт навсегда |
| Reddit r/TelegramBots | ❌ саб забанен админами | мёртвый сабреддит | заменён |
| Reddit r/SideProject | ❌ пост удалён фильтром мгновенно | аккаунт stas_k_v: карма 1, 0 постов | Reddit → трек набора кармы, не блокирует |
| GitHub + SDK | ✅ работает | - | продолжаем |

**Корневой вывод:** каналы с модерацией/фильтрами (Reddit, Hashnode) требуют
«социального капитала» (карма, история, возраст). На старте мы его не имеем -
поэтому v2 ставит на **безпороговые каналы**: каталоги, директории, GitHub-PR,
SaaS-каталоги. Они не банят, дают бэклинки (SEO) и целевой трафик без кармы.

---

## СДЕЛАНО (не трогаем, фиксируем)

### Фаза 0 - сайт (Коди 22.08)
- [x] llms.txt + llms-full.txt, sitemap.xml, robots.txt, JSON-LD (Organization/WebSite/SoftwareApplication/FAQPage)
- [x] Мета/OG/Twitter/canonical, og-image.png, блок доверия, таблица «Flat vs %», калькулятор, FAQ
- [x] 3 копии sha256-идентичны

### Фаза 0.5 - руки Стаса (закрыто 23.08)
- [x] CF: AI-боты и Managed robots.txt выключены; robots.txt чистый
- [x] GSC: домен добавлен, sitemap отправлен, индексация запрошена
- [x] Bing Webmaster: импорт из GSC

### Неделя 1 (22-24.08)
- [x] GitHub smile-sk + репо `cleantrade-python` (README, telegram_bot, webhook_server, poll_status)
- [x] Node.js пример `express_webhook.js` (e2e-тест ALL PASS), пуш e42ae3e
- [x] SDK-фикс: `make_fastapi_handler` экспортирован, v0.1.1, smoke-тесты, пуш d5f91f0
- [x] Статья Dev.to №1 (22.08) + зеркало Medium (24.08, canonical → Dev.to)
- [x] Hashnode - провал, закрыт (см. диагноз)

---

## НЕДЕЛЯ 1 (продолжение) - 24-28.08: каталоги и директории (безпороговые)

Приоритет = максимум бэклинков/трафика за минимум усилий. Почти всё - регистрация
и карточка продукта, **не требует кармы и не банится**. Мои руки (через браузер)
или готовый текст Стасу.

## ПЛАН ПО ДНЯМ (24-31.08) - черновик на согласование со Стасом

День 1 (24.08, сегодня): Cryptwerk - карточка CleanTrade ✅; processorlist - проверен, подачи нет [~]
День 2 (25.08): Payyd - питч-письмо на hey@payyd.co (формы нет, текст готов - отправка за Стасом); AlternativeTo - ✅ аппликация + альтернативы отправлены (ждут модерацию)
День 3 (26.08): G2 - ✅ профиль CleanTrade ОДОБРЕН (24.08); Software Advice - [~] гео-блок Gartner (Украины нет в списке стран вендора); SourceForge - карточка SDK (в работе 24.08)

Правило выполнения: шаг сделан → отчёт Стасу на проверку → после одобрения галочка.

## СВЕРКА С SEO-БАЗОЙ (Ahrefs/Moz, 24.08) - правки по гайдам

Проверено через knowledge() (Ahrefs 06-link-building, Moz 07-link-building, Ahrefs 02-seo-basics).
Вывод: план v2 в целом СОГЛАСОВАН с гайдами (foundational links для нового сайта - «a few dozen
places», тематическая релевантность, контент). Дополнения, которые гайды требуют добавить:

- [x] **A1. Повторить бэклинки конкурентов** - разведка собрана 24.08, площадки и задачи: см. секцию «A1. Бэклинки конкурентов» ниже. Расширяет Блоки A/B/D.
- [ ] **Якоря в карточках** (Moz: anchor text descriptive, not spammy): в описаниях - бренд «CleanTrade»
  и естественные фразы, НЕ keyword-stuffed («best usdt gateway no fees») - иначе Google заподозрит манипуляцию.
- [ ] **Linkable asset** (Ahrefs: «creating linkable assets»): вынести калькулятор «Flat vs %» на отдельную
  страницу fee-calculator + мини-исследование «USDT gateway fees benchmark 2026» (таблица по 5 шлюзам) -
  на такой контент ссылаются сами.
- [x] **Google Alerts** (Ahrefs Part 5, бесплатно): алерт на «CleanTrade», «cleantrade-python» - ловить
  упоминания и просить ссылку (unlinked mentions). Созданы 28.08 (проверено в DOM: «Мои оповещения (3)»).
- [ ] **Кросс-линки контента** (Ahrefs: «promote your older content»): статья №1 ↔ №2 ↔ №3 ссылаются друг
  на друга; из README GitHub - на статьи; внутренние ссылки (Moz on-page). Планируется 29.08.
- [ ] **Ключи для статей №3/№4** (Ahrefs seo-basics: keyword research): писать под реальные запросы -
  «accept usdt payments telegram bot», «usdt payment gateway api no kyc», «non-custodial crypto payments»,
  «flat fee crypto gateway». Проверить формулировки заголовков по этим фразам.

### A1. Бэклинки конкурентов - разведка 24.08 (Ahrefs: «replicate competitor's backlinks»)

Где размещены наши прямые конкуренты (проверено вживую 24.08):

| Площадка | Что там у конкурентов | Наше действие |
| --- | --- | --- |
| **Cryptwerk** - категория Payment gateways (cryptwerk.com/companies/payment-gateway/) | 216 шлюзов: Cryptomus (#3), NOWPayments (#20), Plisio (#24), CoinGate (#35), CoinPayments, BitPay, Binance Pay, Crypto.com Pay; прямые конкуренты: OxaPay (Telegram), MakePay (non-custodial), BucksBus (~0% fees) | карточка: non-custodial, USDT TRC20, 0% fee, интеграция Telegram bot, фильтр Non-custodial |
| **processorlist.com** (?category=crypto) | Coinbase Commerce, NOWPayments, CoinGate, BTCPay, Plisio, Cryptomus, BitPay, CoinRemitter, Blockonomics, Utrust и ещё ~20 | ❌ нет самостоятельной подачи (курируемый, без контактов) |
| **Payyd** (payyd.co/gateways) - 32 шлюза | NOWPayments, Plisio, Cryptomus, CoinGate, BitPay, BTCPay, PayGate.to, XAIGATE; фильтры No-KYC / Cheapest / Non-Custodial | питч-письмо на hey@payyd.co (формы нет, команда еженедельно перепроверяет шлюзы) |
| **AlternativeTo** | страница NOWPayments (alternativeto.net/software/nowpayments/) - «top 12 alternatives» | CleanTrade как альтернатива NOWPayments / CoinGate / Plisio |
| **G2** | категория «Cryptocurrency Payment Gateway»: Cryptomus, CoinGate, 0xProcessing, Fireblocks | профиль продукта |
| **SourceForge** | продукт Cryptomus + alternatives | карточка SDK (dofollow, DR 93) |
| **Software Advice** | профиль Cryptomus | карточка |
| **Trustpilot** | страницы отзывов Plisio и др. | бизнес-профиль (доверие) |
| **Подборки-статьи** (Techiexpert, finance-monthly, crypto.news, paymento.io) | списки «best crypto gateways 2026»: NOWPayments, BitPay, CoinGate, Coinbase Commerce | питч-письмо: angle 0% fee + non-custodial + TRC20; крючок - fee-calculator и benchmark |

**Прямые конкуренты-продукты (не директории, но обязательны в месседжинге и FAQ):**

| Конкурент | Что это | Наш ответ |
| --- | --- | --- |
| **@CryptoBot / Crypto Pay** | бесплатный нативный провайдер в Telegram (USDT/TON/BTC), инвойсы+вебхуки из коробки; деньги на внутреннем балансе бота | custody: у нас деньги сразу на твой TRC20-кошелёк; твой код (HTTP API), а не Telegram |
| **BTCPay Server** | бесплатный self-host, self-custodial, без KYC | managed API: не надо хостить/синкать ноду - три строки кода, ноль оперирования |
| **NOWPayments** | 0.5%/tx (+0.5% только конвертация), есть non-custodial режим | DX + 0% flat на объёме; не кастодиальный по умолчанию |
| **Plisio** | 0.5%/tx | тот же аргумент DX + безубыток на объёме |

Задачи (новые галочки):
- [x] Cryptwerk: карточка CleanTrade (отправлена 24.08, ждём модерацию - письмо после проверки)
- [~] processorlist.com: НЕТ самостоятельной подачи (курируемый каталог, нет формы/контактов/GitHub, WHOIS закрыт) - пропускаем, вернуться если появится канал
- [x] Payyd: питч-письмо отправлено Стасом 24.08 на hey@payyd.co (Non-Custodial + No-KYC + USDT TRC20 + 0% flat)
- [x] AlternativeTo: аппликация CleanTrade + альтернативы NOWPayments отправлены 24.08 (список конкурентов, ждут одобрения команды)
- [x] G2: профиль CleanTrade ОДОБРЕН для публикации (24.08, DR 90+, прямую ссылку пришлют)
- [~] Software Advice: закрыт 24.08 - G2 Digital Markets (Capterra/Gartner) не пускает Украину: форма вендора принимает только US/CA/UK/AU/NZ, Украина отсутствует в списке
- [x] SourceForge: карточка SDK опубликована (sourceforge.net/p/cleantrade, DR~93, импорт из GitHub + OAuth; полное описание - проверить при случае)
- [x] Trustpilot: бизнес-профиль закрыт (25.08): домен верифицирован мета-тегом, категория Primary = Payment Service подтверждена, описание 220 слов, identity + Google My Business подтверждены; активирован 14-day Plus trial; публичная страница trustpilot.com/review/pay.cleantrade.info (проверять глазами - антибот)
- [x] Питч в подборки закрыт 25.08: Techiexpert - питч гостевой статьи 1000-1200 слов отправлен на hello@techiexpert.com (ждём ответа); finance-monthly - вычеркнут (платно: $300/£265 за статью, ответ Jacob Mallinder 25.08); crypto.news - исключён (только платный Partner Content); paymento.io - исключён (конкурент, не редакция). Итог: из 4 целей бесплатно реален только Techiexpert

Примечание: Coinbase Commerce на Cryptwerk не подтверждён (есть на processorlist) - не блокер.

### Блок A. Крипто-мерчант-директории (целевая аудитория - принимающие крипту)
- [ ] **Cryptwerk** (cryptwerk.com) - крупнейший каталог, 3800+ мерчантов, бесплатно. Карточка: CleanTrade - non-custodial USDT TRC20 gateway, 0% fees
- [ ] **CoinMap** (cryptwerk.com/coinmap) - гео-каталог, та же база
- [ ] **SpendCrypto** merchant directory (spendcrypto.com)
- [ ] **BTCMap** / **Spendabit** - если есть публичная точка приёма

### Блок B. Каталоги API / разработчиков (бэклинки + трафик разрабов)
- [ ] **RapidAPI** - опубликовать API (карточка + документация)
- [ ] **public-apis** (GitHub) - PR с добавлением CleanTrade
- [ ] **awesome-telegram-bots** (GitHub) - PR
- [ ] **awesome-crypto-payments** / **awesome-tron** (GitHub) - PR
- [ ] **api.hub / APILayer** - карточка
- [ ] **apis.guru** - добавить спецификацию (если есть OpenAPI)

### Блок C. Telegram-бот-директории (аудитория владельцев ботов) - ЗАКРЫТ ✅ 28.08 (4/4)
- [x] **StoreBot** (storebot.me) - крупнейший каталог ботов - карточка @CleanTrade_bot отправлена 28.08 (реальные команды из bots/sales.py, логотип; сабмит Стас; ждём ревью ~1 неделя)
- [x] **tg.bot** (human-reviewed, без платных рангов) - отправлено 28.08: форма = генератор mailto на email@example.com, письмо собрано и отправлено через Gmail-композер (сабмит Стас); ревью до 72 часов
- [x] **tdirectory.me** - отправлено 28.08: модалка Submit a Channel/Group/Bot (First Name/Email/Message), капча и сабмит - Стас; ответ: Success, review and publish shortly
- [~] **botlist.net** - пропущен 28.08: сайт полумёртвый (Redis-ошибки, битые картинки ботов), Google-логин сломан (href="#" без обработчика), работает только Discord (аккаунта нет) - вернуться, если оживёт
- [x] **tgdr** (tgdr.io, репо thedevs-network/tgdr) - отправлено 28.08: репо = код сайта (Next.js+MongoDB), боты в БД → подача только через сайт, GitHub-PR невозможен; вход через Telegram OAuth (Стас), форма (username/category/title/description) заполнена мной, сабмит Стас; ответ: Submitted, waiting for review

### Блок D. SaaS/стартап-каталоги (самые ценные dofollow-бэклинки)
- [x] **SaaSHub** (DR 72, free) - карточка CleanTrade отправлена (25.08) + ДОЗАПОЛНЕНА по запросу ревьюеров (28.08: конкуренты NOWPayments/CoinGate/Plisio.net/OxaPay, 3 категории, лого, прайс, Q&A, фичи, гит вместо LinkedIn) - ждём повторного ревью ~1 нед
- [ ] **AlternativeTo** - «Stripe alternative for crypto» (наш угол)
- [x] **BetaList** (DR 78) - запуск: Lite $39, подан 25.08, в очереди на ревью до 2 недель
- [x] **Startup Stash** (DR 76) - карточка CleanTrade отправлена (25.08: форма, название/описание до 500 символов)
- [x] **Crunchbase** - профиль CleanTrade создан (25.08: компания + overview: сайт, описание, год основания, отрасли Financial Services/Software; заявка принята, публично через несколько минут)
- [ ] **SourceForge** (DR 93) - проект SDK
- [x] **Uneed** - карточка CleanTrade заполнена (SEO-описание, теги, логотип) и LAUNCH SCHEDULED:
      10.09.2026 12:00 AM PST (26.08, skip-the-line $14.99); листинг бесплатный (freemium),
      ссылка NOFOLLOW (блок D это трафик/упоминание, не SEO-вес), DR 62-73

### Блок E. Dev-комьюнити (разовые, с подготовкой)
- [ ] **Show HN** - ОТЛОЖЕН до демо-песочницы (правило «не лендинг», один выстрел - не жечь).
      Предпосылка: pay.cleantrade.info/demo - создать заказ / симулировать оплату / увидеть webhook
      БЕЗ регистрации (задача в Неделю 3)
- [x] **Indie Hackers** - проверен 26.08: жив, само-промо = суть платформы (Submit a Post, Add Yours),
      посты свежие; аудитория = инди-девы/микро-SaaS (наш ICP). Нюанс: dofollow в профиле только
      при >2000 очков, в постах обычно nofollow → это воронка ЛЮДЕЙ, не SEO.
      Задача: пост-история «dogfooding on my own bot» (Неделя 2)
- [ ] **HackerNoon** - проверен 26.08: DR 88, dofollow, бесплатно, ~1.2M визитов/мес, крипто/дев-аудитория;
      минус - редакторская проверка ~3 раб. дня, могут отредактировать; ДО подачи проверить политику
      репостов (можно ли публиковать уже вышедшую на Dev.to статью). Задача: репост статьи №3 (Неделя 2)
- [ ] **Product Hunt** (DR 91) - страница + скриншоты + 1-мин видео (готовить ассеты до запуска)
- [ ] **Lobste.rs** - если найдём инвайт

### Блок F. Контент (Dev.to/Medium - работает, продолжаем)
- [x] Статья №2: персональная история «сисадмин → разработчик платежей» (опубликована 25.08 на Dev.to + Medium; EN: docs/devto_article_02_personal_story_EN.md, RU-версия для чтения: docs/devto_article_02_personal_story_RU.md)
- [x] Статья №3: «Flat-fee vs percentage crypto gateways: honest math for micro-SaaS» - ОПУБЛИКОВАНА 26.08 (Dev.to + Medium, canonical → Dev.to):
      docs/devto_article_03_final.md (Dev.to) + docs/devto_article_03_final.html (Medium);
      крючок, реальный SDK-код (create_order), таблицы, CTA; честность: 0.5%, безубыток $2k/$6k,
      «когда процентник выигрывает». Обложка - промпт wide 1000×420 дан
- [ ] Статья №4: «Building a non-custodial paywall in Python with TRC20 webhooks» (архитектура) - была №3, сдвинута
- [ ] Подборка: «Top 5 USDT payment gateways for Telegram bots in 2026» (включаем себя честно) - для ИИ-поиска/GEO

---

## НЕДЕЛЯ 2 (29.08-04.09): GEO / ИИ-поиск + доверие

- [ ] Кросс-линки контента (перенесено с 28.08): статья №1 ↔ №2 ↔ №3 + из README GitHub - блоки готовые даю я, вставляет Стас
- [ ] Подборка №2: «Stripe alternatives for crypto: non-custodial options»
- [ ] Проверить бенчмарк-запрос в ChatGPT/Perplexity/Gemini: «easiest non-custodial USDT TRC20 payment API with no percentage fees» - записать, кто рекомендует (метрика ИИ-видимости)
- [ ] Написать авторам 3 существующих подборок - вежливо предложить добавить CleanTrade
- [ ] Status-страница: status.cleantrade.info (UptimeRobot free, проверка /healthz)
- [ ] Секция «Security & Privacy» на сайте (что НЕ храним, как работают X-API-Key)
- [ ] PyPI: `cleantrade-python` (pip install) + npm: `cleantrade-node`
- [ ] MCP-сервер / промпт для AI-агентов (Cursor/Claude) - «payments for AI agents»

---

## НЕДЕЛЯ 3 (05-11.09): конверсия и виральность

- [ ] Демо-песочница pay.cleantrade.info/demo - создать заказ / симулировать оплату / увидеть webhook
      БЕЗ регистрации (предпосылка Show HN)
- [ ] Реферальная программа: +20 заказов/мес за бейдж «Powered by CleanTrade»
- [ ] Бейдж SVG + код вставки
- [ ] Case Study №1: «Как CleanTrail-бот продаёт индикатор TradingView через CleanTrade» (реальный догфуд)
- [ ] Витрина SaaSHub / AlternativeTo - апдейт по факту появления первых пользователей

---

## НЕДЕЛЯ 4 (12-18.09): масштабирование

- [ ] Второй Show HN (при заходе первого) или апдейт-пост
- [ ] Гостевой пост в 1-2 профильных блога про ботов
- [ ] Партнёрка с TG-каналами разработчиков (точечно, малый бюджет)
- [ ] Итоговый замер: визиты, подписки, MRR, ИИ-упоминания → docs/metrics.md
- [ ] Решение: PHP/Go SDK, TON, новые сети, цена

---

## Reddit - отдельный трек набора кармы (НЕ блокирует основной план)

Цель: оживить аккаунт stas_k_v (карма 1) до кармы ~50-100, чтобы посты перестали
улетать в спам-фильтр. Параллельно, 5-10 мин/день.

- [ ] Комментарии в r/Telegram, r/bots, r/Chatbots, r/Python, r/webdev (помощь по USDT/платежам/ботам, без рекламы)
- [ ] Через 1-2 недели - пост в r/SideProject (текст уже готов) + r/MicroSaaS (экономический угол)
- [ ] НЕ постить ссылки в теле поста - только первым комментарием

---

## CleanTrail (индикатор/бот) - отдельный трек: CMC Community

Платёжку на CMC НЕ пихать (аудитория - трейдеры, не разрабы). CMC - площадка под индикатор/бота:
профиль Stanislav_K (coinmarketcap.com/community/profile/Stanislav_K) - посты-разборы SOL/BTC
набирали 1.7-2K organic при 1 подписчике, био ведёт на @CleanTrade_bot (проверено 26.08).

- [ ] Агент для автоматического анализа рынка и постинга на CMC (инструменты у Стаса есть;
      сделать позже - заметка в памяти 26.08)
- [ ] Возобновить трейдерский контент вручную (разборы SOL/BTC с припиской «More info on Telegram»)

---

## Куда постить (шпаргалка v2)

| Площадка | Тип | Порог входа | Что даёт |
| --- | --- | --- | --- |
| Cryptwerk / CoinMap | крипто-директория | нет | целевые мерчанты |
| StoreBot / tg.bot / tdirectory / tgdr | TG-каталог | нет (tgdr - Telegram OAuth) | владельцы ботов |
| RapidAPI / public-apis / api.hub | каталог API | нет | разрабы + бэклинк |
| SaaSHub / AlternativeTo / BetaList / Startup Stash / Crunchbase / SourceForge / Uneed | SaaS-каталог | нет | dofollow-бэклинки (DR 62-93) |
| GitHub (awesome-*, public-apis PR) | PR | нет | разрабы |
| Dev.to / Medium / HackerNoon | контент | есть аккаунт | трафик + ИИ-поиск (HackerNoon - dofollow) |
| Show HN | запуск | нет | всплеск трафика - один выстрел, после демо-песочницы |
| Indie Hackers | запуск/история | нет | живые инди-девы (воронка, не SEO) |
| Product Hunt | запуск | через Google | всплеск трафика |
| Reddit | истории/помощь | **нужна карма** | отложено до кармы |

## Метрики успеха (30 дней, реалистичные)

- [ ] ≥500 уникальных визитов (GSC/аналитика)
- [ ] 3-5 подписок Free
- [ ] ≥1 платный подписчик (Starter+)
- [ ] 5+ живых карточек в каталогах (Cryptwerk, SaaSHub, AlternativeTo, BetaList, Crunchbase)
- [ ] Упоминание в ответе хотя бы одной ИИ-модели на бенчмарк-запрос
- [ ] 20-50 звёзд GitHub

---

## Внеплановые апдейты

- [x] 28.08: БЛОК C ЗАКРЫТ ✅ ПОЛНОСТЬЮ 4/4 - StoreBot + tg.bot + tdirectory + tgdr (tgdr.io, Telegram OAuth, сабмит Стас) отправлены; botlist.net пропущен (мёртвый: Redis-ошибки, битые картинки, Google-логин href="#", только Discord)
- [x] 28.08: Google Alerts - созданы «CleanTrade» и «cleantrade-python» (email@example.com, раз в день, лучшие результаты; проверено в DOM: «Мои оповещения (3)»)
- [x] 28.08: tdirectory.me - карточка @CleanTrade_bot отправлена (модалка Submit a Channel/Group/Bot: First Name/Email/Message; капча и сабмит - Стас; ответ: Success, review and publish shortly) - Блок C шаг 3/4
- [x] 28.08: tg.bot - карточка отправлена (форма = генератор mailto на email@example.com; письмо собрано через Gmail-композер в браузере, отправил Стас; ревью до 72 часов) - Блок C шаг 2/4
- [x] 28.08: StoreBot - карточка @CleanTrade_bot заполнена и отправлена (реальные команды из кода: /start /buy /status /mytoken /help /support; описание 265 симв.; логотип приложил Стас; сабмит Стасом; ответ: ждём ревью ~1 неделя) - Блок C шаг 1/4
- [x] 26.08: Uneed - карточка CleanTrade (SEO-описание self-custody/no-KYC/flat-fee, теги, логотип, GitHub SDK) + LAUNCH SCHEDULED: 10.09.2026 12:00 AM PST (skip-the-line $14.99)
- [x] 26.08: Статья №3 опубликована Стасом (Dev.to + Medium, canonical → Dev.to)

- [x] 22.08: webhook MAX_ATTEMPTS 3→5, годовые тарифы ×10, фикс калькулятора, og-image заменён
- [x] 22.08: репо cleantrade-python + статья Dev.to №1
- [x] 23.08: SDK-фикс make_fastapi_handler (v0.1.1) + smoke-тесты + Node.js пример e42ae3e
- [x] 24.08: Medium-зеркало №1 (canonical → Dev.to). Hashnode - провал (закрыт). Reddit r/SideProject - пост удалён фильтром (аккаунт холодный) → Reddit вынесен в отдельный карма-трек
- [x] 24.08: A1 - разведка ссылок конкурентов
- [x] 24.08: Cryptwerk - карточка CleanTrade отправлена (ждём модерацию)
- [x] 24.08: processorlist - проверен, НЕТ самостоятельной подачи (курируемый каталог) - задача закрыта как неподъёмная
- [x] 24.08: Payyd - проверен: формы подачи нет, но есть контакт hey@payyd.co → задача переформулирована в питч-письмо
- [x] 24.08: AlternativeTo - аппликация CleanTrade + альтернативы NOWPayments отправлены (ждут одобрения)
- [x] 24.08: G2 - профиль CleanTrade ОДОБРЕН для публикации (DR 90+, прямую ссылку пришлют на почту)
- [x] 25.08: Trustpilot - бизнес-профиль полностью настроен (см. A1)
- [x] 25.08: Статья №2 - персональная история (сисадмин → разработчик платежей) опубликована на Dev.to + Medium (canonical → Dev.to)
- [x] 25.08: Питч в подборки - finance-monthly (готовая статья flat-rate, .docx) + Techiexpert (питч гостевой статьи); crypto.news/paymento.io исключены (платный партнёрский контент / конкурент)
- [x] 25.08: SaaSHub - карточка CleanTrade отправлена (продукт подан, verified alternative на страницах NOWPayments/CoinGate/Plisio.net/OxaPay)
- [x] 25.08: BetaList - запуск CleanTrade подан (Lite $39, 25.08, в очереди на ревью до 2 недель)
- [x] 28.08: SaaSHub - карточка дозаполнена по запросу (конкуренты 4, категории 3, лого, прайс, Q&A, фичи, гит вместо LinkedIn) - ждём повторного ревью ~1 нед
- [x] 25.08: Startup Stash - карточка CleanTrade отправлена (форма подачи, ждём ответа)
- [x] 25.08: Crunchbase - профиль CleanTrade создан (компания + overview: сайт, описание, 2026, отрасли Financial Services/Software; заявка принята)
- [x] 25.08: finance-monthly - вычеркнут из питча (платно: $300/£265 за статью, ответ Jacob Mallinder); итог подборок: бесплатно реален только Techiexpert (ждём ответа)
- [x] 24.08: план переписан в v2 (реальные безпороговые каналы вместо Reddit/Hashnode)
- [x] 25.08: внешняя ревизия принята - сдвиг позиционирования с цены на DX/self-custody; честный калькулятор (0.5%, Starter-default, продажи×чек); aup.html; оплата подписок = dogfood
- [x] 26.08: Деплой честной версии на VPS (калькулятор 0.5% + Starter-default, таблица 0.5-1%, FAQ @CryptoBot/BTCPay, aup.html, llms*, sitemap) - байт-в-байт, HTTP 200
- [x] 26.08: GSC-диагностика «Не получено»: причина = Browser Integrity Check (CF резал не-браузерных клиентов, включая фетчер GSC; Googlebot для страниц проходил - страница в индексе). BIC выключен Стасом 26.08 - sitemap отдаётся 200 всем UA. sitemap_index.xml создан (опционален, для мгновенного рефетча; Стас решил не плодить - ждём ретрай GSC)
- [x] 26.08: Локальная cleantrade.info_landing синхронизирована с VPS (57 файлов, md5 - 0 расхождений; старая версия в _TEMP/backups/cleantrade.info_landing.bak_20260826_164703)
- [x] 26.08: Статья №3 - финал (md + html), публикация за Стасом; разведка площадок: IH ✅ (жив, само-промо), HackerNoon ✅ (DR88 dofollow, ~3 дня, проверить репост-политику), Show HN ⏸ (до демо-песочницы)
- [x] 26.08: Uneed - проверен: листинг бесплатный (freemium), ссылка NOFOLLOW (блок D поправлен), DR 62-73 по источникам; подача - когда дойдём до блока D
