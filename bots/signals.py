"""CleanTrade Signals - все сигнальщики в одном модуле.

Четыре монитора в одном процессе, один Telegram-бот (@HexusCrypto_bot):
  * impulse - импульсы (ATR-нормализованная цена + RVOL-объём + мягкий OI), 1h WebSocket
  * fvg     - Fair Value Gaps (1h), Bybit WebSocket
  * liq     - ликвидации $100K+, Bybit WebSocket
  * session - открытие/закрытие торговых сессий (NY/London/Tokyo/Sydney)

Алерты шлются только авторизованным (data/authorized_users.txt). Список
перечитывается при каждой рассылке - свежий доступ начинает работать сразу,
без перезапуска.
"""
import asyncio
import json
import logging
import time
from collections import deque
from datetime import datetime

import pytz
import requests
import schedule
import telegram
from telegram.error import Forbidden
import websockets

import config
from core import auth as token_store
from core.log import setup_logger

logger = setup_logger("signals", "signals.log")

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "1000PEPEUSDT",
    "LDOUSDT", "ADAUSDT", "BNBUSDT", "WIFUSDT", "LINKUSDT", "ENAUSDT",
    "LTCUSDT", "ONDOUSDT",
]
WEBSOCKET_URL = "wss://stream.bybit.com/v5/public/linear"


async def send_to_authorized(bot: telegram.Bot, text: str,
                             parse_mode=telegram.constants.ParseMode.MARKDOWN) -> int:
    """Разослать всем авторизованным. Возвращает число получателей.

    Заблокировавший бота пользователь помечается в data/blocked_users.txt:
    доступ у него СОХРАНЯЕТСЯ (он платил), но рассылка приостанавливается,
    чтобы не долбить в стену и не спамить лог. Напишет боту снова - флаг снимется."""
    users = token_store.authorized_users()
    blocked = token_store.blocked_users()
    for uid in users:
        if str(uid) in blocked:
            continue
        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode=parse_mode)
        except Forbidden:
            token_store.mark_blocked(uid)
            logger.warning(
                "Пользователь %s заблокировал бота - доступ сохранён, рассылка приостановлена", uid
            )
        except Exception as exc:
            logger.error("Не удалось отправить %s: %s", uid, exc)
    return len(users)


# ---------------- Impulse: ATR-нормализованные импульсы ----------------
# Идея: свеча считается импульсом, если её тело (|close-open|) не меньше
# K*ATR(14) И текущий объём не меньше RVOL_THRESHOLD от среднего за RVOL_PERIOD
# свечей. ATR сам подстраивается под волатильность каждой монеты: BTC ловит
# только крупные для BTC движения, 1000PEPE - крупные для PEPE. OI - мягкое
# справочное подтверждение (никогда не блокирует сигнал).
IMPULSE_TIMEFRAME = "60"  # 1h; "30"=30m, "15"=15m (45m у Bybit нет)       # интервал свечи. Bybit v5: 1/3/5/15/30/60/120/240/360/720/D/W/M
                               # (45m у Bybit НЕТ). Час = "60", 30 мин = "30".
ATR_PERIOD = 14                # период ATR
ATR_MULTIPLIER = 1.5           # K: тело свечи >= K*ATR
RVOL_PERIOD = 20               # база объёма (SMA по объёму)
RVOL_THRESHOLD = 1.5           # текущий объём >= 1.5 * средний
OI_PATTERN_THRESHOLD = 0.5       # |ΔOI| ниже этого - считаем нейтральным (паттерн flat)
HISTORY_LIMIT = 60             # свечей подтянуть при старте (REST)
IMPULSE_COOLDOWN_CANDLES = 2   # пропуск N свечей после алерта (против спама)
FIB_LOOKBACK = 20               # свечей для поиска локального свинга (для Fib-тейков)
FIB_LEVELS = (0.382, 0.618, 1.0)  # расширения измеренного хода (38.2% / 61.8% / 100%)
SIDEWAYS_ENABLED = True           # фильтр боковика: импульсы в боковике пропускаются
SIDEWAYS_LOOKBACK = 20            # свечей истории для оценки (Kaufman Efficiency Ratio)
SIDEWAYS_ER_MAX = 0.25            # ER ниже порога = боковик/шум → сигнал не шлём
SIDEWAYS_BREAKOUT_BODY = 2.0    # тело ≥ 2×ATR И RVOL ≥ 2× = мощный пробой боковика → пропускаем
SIDEWAYS_BREAKOUT_RVOL = 2.0    # RVOL-порог для пробоя (в паре с BODY)
BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers"
KLINE_URL = "https://api.bybit.com/v5/market/kline"

oi_history = {}  # symbol -> deque[(ts, openInterest)] - база для OI-подтверждения


def _pct(old, new):
    old, new = float(old), float(new)
    return 0 if old == 0 else (new - old) / old * 100


def _true_range(candles):
    """TR для каждой свечи (кроме первой) в порядке от старой к новой."""
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        try:
            h, l, pc = float(c["high"]), float(c["low"]), float(p["close"])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        except (KeyError, ValueError, TypeError):
            trs.append(0.0)
    return trs


def _atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        return None
    trs = _true_range(candles)[-period:]
    if not trs:
        return None
    return sum(trs) / len(trs)


def _rvol(candles, period=RVOL_PERIOD):
    """RVOL = объём текущей свечи / SMA(объёма) предыдущих period свечей."""
    if len(candles) < period + 1:
        return None
    try:
        clist = list(candles)  # deque не поддерживает срез - приводим к list
        base = [float(c["volume"]) for c in clist[-(period + 1):-1]]
        cur = float(clist[-1]["volume"])
    except (KeyError, ValueError, TypeError):
        return None
    if not base:
        return None
    sma = sum(base) / len(base)
    return None if sma == 0 else cur / sma


def _efficiency_ratio(candles, lookback=SIDEWAYS_LOOKBACK):
    """Kaufman Efficiency Ratio: |сет-ход| / |весь путь| за N свечей.

    0..1. Близко к 0 - боковик (цена ходит туда-сюда: путь >> результата),
    близко к 1 - направленный тренд. Та же метрика, что питает KAMA.
    Считаем по свечам ДО текущей, чтобы импульсная свеча не влияла на оценку."""
    clist = list(candles)
    if len(clist) < lookback + 2:
        return None
    try:
        closes = [float(c["close"]) for c in clist[-(lookback + 1):-1]]
    except (KeyError, ValueError, TypeError):
        return None
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    if path == 0:
        return 0.0
    return abs(closes[-1] - closes[0]) / path


def _fmt_price(p):
    # Адаптивный формат цены: BTC 77413 -> "77,413", PEPE 0.00001234 -> "0.00001234"
    p = float(p)
    if abs(p) >= 1000:
        s = f"{p:,.2f}"
    elif abs(p) >= 1:
        s = f"{p:,.4f}"
    else:
        s = f"{p:.8f}"
    return s.rstrip("0").rstrip(".")


def _fib_targets(candles, close_p, is_bull):
    # Приблизительные тейки: проекция расширений Фибоначчи измеренного хода
    # от локального свинга (история уже прогрета). Возвращает [(price, level), ...]
    clist = list(candles)
    if len(clist) < FIB_LOOKBACK + 1:
        return None
    window = clist[-(FIB_LOOKBACK + 1):-1]  # без текущей (импульсной) свечи
    try:
        if is_bull:
            swing = min(float(c["low"]) for c in window)
            move = close_p - swing
        else:
            swing = max(float(c["high"]) for c in window)
            move = swing - close_p
    except (KeyError, ValueError, TypeError):
        return None
    if move <= 0:
        return None
    return [(close_p + move * f if is_bull else close_p - move * f, f) for f in FIB_LEVELS]


def _oi_change(symbol):
    q = oi_history.get(symbol)
    if not q or len(q) < 2:
        return None
    return _pct(q[0][1], q[-1][1])


def _oi_pattern(is_bull, oi_change):
    """Подсказка новичку: что значит связка направление цены × знак OI."""
    if oi_change is None:
        return None
    if abs(oi_change) < OI_PATTERN_THRESHOLD:
        return "🔎 Pattern: flat OI - move on existing positions, no fresh money"
    if is_bull:
        if oi_change > 0:
            return "🔎 Pattern: bullish conviction - price up + OI up, fresh longs entering"
        return "🔎 Pattern: short squeeze - price up + OI down, shorts covering"
    if oi_change > 0:
        return "🔎 Pattern: bearish conviction - price down + OI up, fresh shorts entering"
    return "🔎 Pattern: long squeeze - price down + OI down, longs exiting"


def _impulse_signal(symbol, candles):
    atr = _atr(candles)
    rvol = _rvol(candles)
    if atr is None or atr <= 0 or rvol is None:
        return None
    kline = candles[-1]
    try:
        open_p = float(kline["open"])
        close_p = float(kline["close"])
        body = abs(close_p - open_p)
    except (KeyError, ValueError, TypeError):
        return None
    if body < ATR_MULTIPLIER * atr:
        return None
    if rvol < RVOL_THRESHOLD:
        return None
    if SIDEWAYS_ENABLED:
        er = _efficiency_ratio(candles)
        if er is not None and er < SIDEWAYS_ER_MAX:
            breakout = (body >= SIDEWAYS_BREAKOUT_BODY * atr) and (rvol >= SIDEWAYS_BREAKOUT_RVOL)
            if breakout:
                logger.info("Impulse %s: sideways ER=%.2f but strong breakout (body %.1fxATR, rvol %.1f) - allowed", symbol, er, body / atr, rvol)
            else:
                logger.info("Impulse %s: skipped (sideways ER=%.2f < %.2f)", symbol, er, SIDEWAYS_ER_MAX)
                return None
    is_bull = close_p > open_p
    direction = "Bullish alert 📈" if is_bull else "Bearish alert 📉"
    pct = (close_p - open_p) / open_p * 100 if open_p else 0
    stop = close_p - atr if is_bull else close_p + atr  # ориентир ATR-стопа
    return {
        "symbol": symbol,
        "direction": direction,
        "last_price": close_p,
        "pct": pct,
        "body": body,
        "atr": atr,
        "rvol": rvol,
        "stop": stop,
        "fib": _fib_targets(candles, close_p, is_bull),
        "oi_change": _oi_change(symbol),
        "is_bull": is_bull,
    }


async def _seed_history(history):
    """Подтянуть историю свечей через REST, чтобы ATR/RVOL были готовы сразу."""
    session = requests.Session()
    for symbol in SYMBOLS:
        try:
            resp = session.get(
                KLINE_URL,
                params={"category": "linear", "symbol": symbol,
                        "interval": IMPULSE_TIMEFRAME, "limit": HISTORY_LIMIT},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                logger.error("Impulse seed %s: %s", symbol, data.get("retMsg"))
                continue
            rows = data["result"]["list"]                       # новые первыми
            rows = sorted(rows, key=lambda r: int(r[0]))        # старые первыми
            for r in rows:
                history[symbol].append({
                    "start": int(r[0]), "open": r[1], "high": r[2],
                    "low": r[3], "close": r[4], "volume": r[5],
                })
            logger.info("Impulse seed %s: %d свечей", symbol, len(rows))
        except Exception as exc:
            logger.error("Impulse seed %s: %s", symbol, exc)
        await asyncio.sleep(0.2)


async def run_oi_poller():
    """Раз в 60с снимает openInterest (tickers) - база для мягкого OI-подтверждения."""
    session = requests.Session()
    while True:
        try:
            resp = session.get(BYBIT_TICKERS_URL, params={"category": "linear"}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("retCode") != 0:
                logger.error("OI poller Bybit error: %s", data.get("retMsg"))
            else:
                for item in data["result"]["list"]:
                    symbol = item.get("symbol")
                    if symbol not in SYMBOLS:
                        continue
                    try:
                        oi = float(item.get("openInterest", 0))
                    except (ValueError, TypeError):
                        continue
                    oi_history.setdefault(symbol, deque(maxlen=RVOL_PERIOD + 5)).append((time.time(), oi))
        except Exception as exc:
            logger.error("OI poller error: %s", exc)
        await asyncio.sleep(60)


async def run_impulse(bot: telegram.Bot):
    logger.info("Impulse monitor started (tf=%s)", IMPULSE_TIMEFRAME)
    history = {s: deque(maxlen=HISTORY_LIMIT) for s in SYMBOLS}
    cooldown_left = {s: 0 for s in SYMBOLS}
    last_seen = {}  # symbol -> start последней обработанной свечи (дедуп WS)
    await _seed_history(history)
    reconnect_delay = 5
    while True:
        ws = None
        try:
            ws = await websockets.connect(WEBSOCKET_URL, ping_interval=20, ping_timeout=10)
            await ws.send(json.dumps({"op": "subscribe", "args": [f"kline.{IMPULSE_TIMEFRAME}.{s}" for s in SYMBOLS]}))
            reconnect_delay = 5
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(msg)
                if "topic" not in data or "data" not in data or not data["topic"].startswith("kline."):
                    continue
                symbol = data["topic"].split(".")[-1]
                if symbol not in SYMBOLS:
                    continue
                kline = data["data"]
                if isinstance(kline, list):
                    kline = kline[0] if kline else None
                if not kline or not kline.get("confirm"):
                    continue
                start = int(kline.get("start", 0))
                if last_seen.get(symbol) == start:
                    continue
                last_seen[symbol] = start
                history[symbol].append(kline)
                if cooldown_left[symbol] > 0:
                    cooldown_left[symbol] -= 1
                    continue
                sig = _impulse_signal(symbol, history[symbol])
                if not sig:
                    continue
                cooldown_left[symbol] = IMPULSE_COOLDOWN_CANDLES
                lines = [
                    f"📢 *{sig['symbol']}*: {sig['direction']}",
                    "",
                    f"💰 Price: *${_fmt_price(sig['last_price'])}* ({sig['pct']:+.2f}%)",
                    f"📏 Move: *{_fmt_price(sig['body'])}* ≥ {ATR_MULTIPLIER}×ATR ({_fmt_price(sig['atr'])})",
                    f"📊 Volume: *{sig['rvol']:.2f}×* vs avg",
                ]
                if sig["oi_change"] is not None:
                    lines.append(f"🔥 OI: *{sig['oi_change']:+.2f}%*")
                    pat = _oi_pattern(sig["is_bull"], sig["oi_change"])
                    if pat:
                        lines.append(pat)
                lines.append("")
                if sig["fib"]:
                    n = len(sig["fib"])
                    for i, (price, f) in enumerate(reversed(sig["fib"]), 1):
                        lines.append(f"🎯 TP{n - i + 1} ({f:.0%}): ${_fmt_price(price)}")
                lines.append(f"🛑 SL: *${_fmt_price(sig['stop'])}*")
                await send_to_authorized(bot, "\n".join(lines))
                logger.info("Impulse: %s %s (move %.4g, rvol %.2f)",
                            sig["symbol"], sig["direction"], sig["body"], sig["rvol"])
        except asyncio.TimeoutError:
            logger.warning("Impulse таймаут, переподключение")
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("Impulse соединение закрыто (%s), переподключение", exc)
        except Exception as exc:
            logger.error("Impulse error: %s", exc)
        finally:
            if ws and not ws.closed:
                await ws.close()
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


# ---------------- FVG: Fair Value Gaps (15m) ----------------
FVG_TIMEFRAME = "60"  # 1h - как импульсы
FVG_PRICE_CHANGE_THRESHOLD = 1


def _detect_fvg(candles):
    if len(candles) < 3:
        return None
    try:
        prev2, current = candles[-3], candles[-1]
        if not all(k in prev2 for k in ("high", "low")):
            return None
        if not all(k in current for k in ("high", "low")):
            return None
        if float(prev2["low"]) > float(current["high"]):
            return "Bearish FVG 🐻"
        if float(prev2["high"]) < float(current["low"]):
            return "Bullish FVG 🐂"
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("FVG анализ: %s", exc)
    return None


def _price_change_exceeds(kline):
    try:
        change = abs((float(kline["close"]) - float(kline["open"])) / float(kline["open"]) * 100)
        return change >= FVG_PRICE_CHANGE_THRESHOLD
    except (KeyError, ValueError, TypeError):
        return False


async def run_fvg(bot: telegram.Bot):
    logger.info("FVG monitor started")
    history = {s: [] for s in SYMBOLS}
    reconnect_delay = 5
    while True:
        ws = None
        try:
            ws = await websockets.connect(WEBSOCKET_URL, ping_interval=20, ping_timeout=10)
            await ws.send(json.dumps({"op": "subscribe", "args": [f"kline.{FVG_TIMEFRAME}.{s}" for s in SYMBOLS]}))
            reconnect_delay = 5
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(msg)
                if "topic" not in data or "data" not in data or not data["topic"].startswith("kline."):
                    continue
                symbol = data["topic"].split(".")[-1]
                if symbol not in SYMBOLS:
                    continue
                kline = data["data"]
                if isinstance(kline, list):
                    kline = kline[0] if kline else None
                if not kline or not kline.get("confirm"):
                    continue
                history[symbol].append(kline)
                if len(history[symbol]) > 3:
                    history[symbol].pop(0)
                if _price_change_exceeds(kline):
                    direction = _detect_fvg(history[symbol])
                    if direction:
                        change = (float(kline["close"]) - float(kline["open"])) / float(kline["open"]) * 100
                        await send_to_authorized(
                            bot,
                            f"📢 *{symbol}*: {direction}\n\n"
                            f"💵 Open: {kline['open']}\n"
                            f"💰 Close: {kline['close']}\n"
                            f"⬆️ High: {kline['high']}\n"
                            f"⬇️ Low: {kline['low']}\n"
                            f"📊 Volume: {kline['volume']}\n"
                            f"📈 Price change: {change:.2f}%",
                        )
                        logger.info("FVG: %s %s", symbol, direction)
        except asyncio.TimeoutError:
            logger.warning("FVG таймаут, переподключение")
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("FVG соединение закрыто (%s), переподключение", exc)
        except Exception as exc:
            logger.error("FVG error: %s", exc)
        finally:
            if ws and not ws.closed:
                await ws.close()
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


# ---------------- Ликвидации $100K+ ----------------
MIN_LIQUIDATION_VALUE = 100000


async def run_liq(bot: telegram.Bot):
    logger.info("Liquidation monitor started")
    reconnect_delay = 5
    while True:
        try:
            async with websockets.connect(WEBSOCKET_URL, ping_interval=20, ping_timeout=10) as ws:
                await ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in SYMBOLS]}))
                reconnect_delay = 5
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=60)
                        data = json.loads(msg)
                        if data.get("op") == "ping":
                            await ws.send(json.dumps({"op": "pong"}))
                            continue
                        if "topic" not in data or "allLiquidation" not in data["topic"]:
                            continue
                        items = data.get("data", [])
                        items = items if isinstance(items, list) else [items]
                        for item in items:
                            try:
                                symbol = item.get("s", "")
                                side = item.get("S", "")
                                price = float(item.get("p", 0))
                                size = float(item.get("v", 0))
                                value = price * size
                                if value < MIN_LIQUIDATION_VALUE:
                                    continue
                                side_display = "LONG 🔴" if side == "Buy" else "SHORT 🟢"
                                await send_to_authorized(
                                    bot,
                                    f"🔥 *{symbol}*: {side_display} Liquidated\n\n"
                                    f"📊 Volume: *{size:.2f} (${value:,.0f})*\n"
                                    f"💰 Price: *${price}*\n",
                                )
                                logger.info("Liq: %s $%.0f (%s)", symbol, value, side)
                            except (ValueError, TypeError) as exc:
                                logger.error("Liq parse: %s", exc)
                    except asyncio.TimeoutError:
                        await ws.send(json.dumps({"op": "ping"}))
                    except websockets.exceptions.ConnectionClosed as exc:
                        logger.warning("Liq соединение закрыто (%s), переподключение", exc)
                        break
                    except Exception as exc:
                        logger.error("Liq inner error: %s", exc)
                        break
        except Exception as exc:
            logger.error("Liq connection error: %s", exc)
        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(reconnect_delay * 2, 60)


# ---------------- Сессии (NY/London/Tokyo/Sydney) ----------------
NY_TZ = pytz.timezone("America/New_York")
LONDON_TZ = pytz.timezone("Europe/London")
TOKYO_TZ = pytz.timezone("Asia/Tokyo")
SYDNEY_TZ = pytz.timezone("Australia/Sydney")

CALENDAR_ICON = "📅"
CLOCK_ICON = "🕒"
EXCHANGE_ICON = "💹"


def _session_send(text: str):
    users = token_store.authorized_users()
    blocked = token_store.blocked_users()
    for uid in users:
        if str(uid) in blocked:
            continue
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{config.ACCESS_BOT_TOKEN}/sendMessage",
                json={"chat_id": uid, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
            if resp.status_code == 403:
                token_store.mark_blocked(uid)
                logger.warning(
                    "Пользователь %s заблокировал бота - доступ сохранён, рассылка приостановлена", uid
                )
        except Exception as exc:
            logger.error("Session send %s: %s", uid, exc)


def _make_session_job(city_icon, city, event, tz):
    def job():
        now = datetime.now(tz)
        _session_send(
            f"{EXCHANGE_ICON} *{city_icon} {city} {event}* {EXCHANGE_ICON}\n"
            f"{CLOCK_ICON} Time: *{now.strftime('%H:%M')}*\n"
            f"{CALENDAR_ICON} Date: *{now.strftime('%d.%m.%Y')}*"
        )
    return job


def _build_schedule():
    sched = schedule.Scheduler()
    # Время - серверное (UTC на VPS), как было в оригинале.
    sched.every().day.at("15:00").do(_make_session_job("🗽", "New York", "Market Open", NY_TZ))
    sched.every().day.at("23:00").do(_make_session_job("🗽", "New York", "Market Close", NY_TZ))
    sched.every().day.at("09:00").do(_make_session_job("🇬🇧", "London", "Market Open", LONDON_TZ))
    sched.every().day.at("17:00").do(_make_session_job("🇬🇧", "London", "Market Close", LONDON_TZ))
    sched.every().day.at("02:00").do(_make_session_job("🗼", "Tokyo", "Market Open", TOKYO_TZ))
    sched.every().day.at("10:00").do(_make_session_job("🗼", "Tokyo", "Market Close", TOKYO_TZ))
    sched.every().day.at("23:00").do(_make_session_job("🇦🇺", "Sydney", "Market Open", SYDNEY_TZ))
    sched.every().day.at("07:00").do(_make_session_job("🇦🇺", "Sydney", "Market Close", SYDNEY_TZ))
    return sched


async def run_session():
    logger.info("Session monitor started")
    sched = _build_schedule()
    while True:
        sched.run_pending()
        await asyncio.sleep(1)


# ---------------- Общий запуск ----------------
async def run_all():
    """Запустить все сигнальщики параллельно (один общий Bot)."""
    bot = telegram.Bot(token=config.ACCESS_BOT_TOKEN)
    await asyncio.gather(
        run_impulse(bot),
        run_fvg(bot),
        run_liq(bot),
        run_session(),
        run_oi_poller(),
    )
