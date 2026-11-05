"""
Детектор платежей USDT (TRC20) через TronScan (с запасным каналом TronGrid).

Схема приёма: продавец присылает СВОЙ кошелёк в заказе, наша уникальная сумма
отличает его заказ от чужих. Мы смотрим входящие переводы на кошелёк заказа и
сопоставляем: сумма, USDT, время перевода ПОЗЖЕ создания заказа, tx_id ещё не
занят. Один перевод закрывает ровно один заказ.

Почему запрос на КОШЕЛЁК, а не на заказ: у TronScan лимит 1 запрос в секунду
на ключ. Прежняя версия ходила по каждому ждущему заказу отдельно - второй
запрос подряд получал 429 «suspended for N s», пауза продлевалась следующим
циклом, и детект вставал целиком (замер 22.10.2026: с 12:51 до 13:40 все
запросы 429). Теперь на цикл - один запрос на уникальный адрес, ответ кэшируется
и внутри него матчатся все заказы этого адреса.

check() по-прежнему возвращает tx_id или None - обратная совместимость.
"""
import logging
import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import requests

log = logging.getLogger("tronscan")

TRONSCAN_URL = (
    "https://apilist.tronscanapi.com/api/new/token_trc20/transfers"
    "?toAddress={wallet}&limit={limit}&start=0&sort=-timestamp"
)
USDT_CONTRACT = "TRX_ADDRESS"
TRONGRID_URL = (
    "https://api.trongrid.io/v1/accounts/{wallet}/transactions/trc20"
    "?limit={limit}&only_to=true&contract_address=" + USDT_CONTRACT
)
USDT_DECIMALS = 1_000_000   # USDT в TRC20 имеет 6 знаков после запятой
MATCH_TOLERANCE = 0.005     # половина шага уникальных сумм (0.01)

# Сколько секунд TronScan просит подождать, если не написал это в теле ответа.
DEFAULT_RETRY_AFTER = 10.0
MAX_RETRY_AFTER = 30.0


def _ts_seconds(value) -> float:
    """Время перевода в секундах эпохи. TronScan отдаёт миллисекунды."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number > 1e11:      # миллисекунды
        number /= 1000.0
    return number


def _retry_after(response) -> float:
    """Сколько ждать после 429: TronScan пишет «suspended for N s» в тело."""
    text = (getattr(response, "text", "") or "")
    marker = "suspended for"
    if marker in text:
        tail = text.split(marker, 1)[1]
        number = ""
        for char in tail.strip():
            if char.isdigit():
                number += char
            elif number:
                break
        if number:
            return min(float(number) + 0.5, MAX_RETRY_AFTER)
    return DEFAULT_RETRY_AFTER


def _parse_tronscan(data, wallet: str) -> List[dict]:
    out: List[dict] = []
    for tx in (data or {}).get("token_transfers", []) or []:
        if (tx.get("to_address") or "") != wallet:
            continue
        info = tx.get("tokenInfo") or {}
        if info.get("tokenAbbr") != "USDT":
            continue
        quant = tx.get("quant")
        if quant is None:
            continue
        try:
            amount = float(int(quant)) / USDT_DECIMALS
        except (TypeError, ValueError):
            continue
        out.append({
            "tx_id": tx.get("transaction_id") or "",
            "amount": amount,
            "ts": _ts_seconds(tx.get("block_ts") or tx.get("timestamp")),
        })
    return [item for item in out if item["tx_id"]]


def _parse_trongrid(data, wallet: str) -> List[dict]:
    out: List[dict] = []
    for tx in (data or {}).get("data", []) or []:
        if (tx.get("to") or "") != wallet:
            continue
        info = tx.get("token_info") or {}
        if (info.get("address") or USDT_CONTRACT) != USDT_CONTRACT:
            continue
        if info.get("symbol") and info.get("symbol") != "USDT":
            continue
        raw_value = tx.get("value")
        if raw_value is None:
            continue
        try:
            decimals = int(info.get("decimals", 6))
            amount = float(int(raw_value)) / (10 ** decimals)
        except (TypeError, ValueError):
            continue
        out.append({
            "tx_id": tx.get("transaction_id") or "",
            "amount": amount,
            "ts": _ts_seconds(tx.get("block_timestamp")),
        })
    return [item for item in out if item["tx_id"]]


def match_transfer(transfers: List[dict], expected_amount: float,
                   since_ts: float = 0.0, used_tx: Optional[Set[str]] = None,
                   tolerance: float = MATCH_TOLERANCE) -> Optional[str]:
    """tx_id перевода, которым оплачен заказ, или None.

    Условия: сумма совпала, перевод пришёл ПОСЛЕ создания заказа (since_ts),
    tx_id ещё никому не зачтён. Из подходящих берём самый ранний - тогда
    порядок заказов на одном кошельке не зависит от порядка в списке.
    """
    used = used_tx or set()
    best = None
    for item in transfers or []:
        if item.get("tx_id") in used:
            continue
        if float(item.get("ts") or 0) < since_ts:
            continue
        amount = float(item.get("amount") or 0)
        if abs(amount - expected_amount) < tolerance or \
                round(amount, 2) == round(expected_amount, 2):
            if best is None or float(item.get("ts") or 0) < float(best.get("ts") or 0):
                best = item
    return best["tx_id"] if best else None


class PaymentDetector:
    """Входящие USDT-переводы на кошелёк заказа: TronScan, TronGrid запасным."""

    def __init__(self, wallet: str = "", api_key: str = "", mock: bool = False,
                 min_interval: float = 1.1, cache_ttl: float = 20.0,
                 timeout: float = 10.0, limit: int = 50):
        self.wallet = wallet
        self.api_key = api_key
        self.mock = mock
        self.min_interval = max(0.0, float(min_interval))
        self.cache_ttl = max(0.0, float(cache_ttl))
        self.timeout = timeout
        self.limit = limit
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._cache: Dict[str, Tuple[float, List[dict]]] = {}
        # Для журнала аудита: чем ответили в этот раз и почему не ответили.
        self.last_source = ""
        self.last_error = ""

    # --- сеть -----------------------------------------------------------
    def _throttle(self) -> None:
        """Не чаще одного запроса в min_interval секунд (лимит TronScan: 1/с)."""
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = time.monotonic() + self.min_interval

    def _get(self, url: str, headers: Optional[dict] = None):
        self._throttle()
        return self._session.get(url, headers=headers or {}, timeout=self.timeout)

    def _fetch_tronscan(self, wallet: str) -> Optional[List[dict]]:
        url = TRONSCAN_URL.format(wallet=wallet, limit=self.limit)
        headers = {"TRON-PRO-API-KEY": self.api_key} if self.api_key else {}
        for attempt in (1, 2):
            try:
                response = self._get(url, headers)
            except Exception as exc:
                log.warning("TronScan: сеть недоступна (%s)", exc)
                return None
            if response.status_code == 429:
                wait = _retry_after(response)
                log.warning(
                    "TronScan 429 (лимит 1 запрос/с, ключ исчерпан): пауза %.0f c, "
                    "попытка %d/2", wait, attempt,
                )
                if attempt == 1:
                    time.sleep(wait)
                    continue
                return None
            if response.status_code != 200:
                log.warning("TronScan HTTP %s: %s", response.status_code,
                            (response.text or "")[:200])
                return None
            try:
                data = response.json()
            except ValueError:
                log.warning("TronScan вернул не JSON: %s", (response.text or "")[:200])
                return None
            return _parse_tronscan(data, wallet)
        return None

    def _fetch_trongrid(self, wallet: str) -> Optional[List[dict]]:
        url = TRONGRID_URL.format(wallet=wallet, limit=self.limit)
        try:
            response = self._get(url)
        except Exception as exc:
            log.warning("TronGrid: сеть недоступна (%s)", exc)
            return None
        if response.status_code != 200:
            log.warning("TronGrid HTTP %s: %s", response.status_code,
                        (response.text or "")[:200])
            return None
        try:
            data = response.json()
        except ValueError:
            log.warning("TronGrid вернул не JSON: %s", (response.text or "")[:200])
            return None
        return _parse_trongrid(data, wallet)

    # --- кэш на цикл ----------------------------------------------------
    def transfers(self, wallet: Optional[str] = None,
                  force: bool = False) -> Optional[List[dict]]:
        """Входящие USDT-переводы на кошелёк (один запрос на адрес за цикл).

        None - оба канала не ответили; ошибка уже в журнале, повторим в
        следующем цикле. Пустой список - ответили, переводов нет.
        """
        target = (wallet or self.wallet or "").strip()
        if not target:
            log.error("Детектор: не задан кошелёк - проверять нечего")
            return None
        now = time.monotonic()
        if not force:
            cached = self._cache.get(target)
            if cached and (now - cached[0]) < self.cache_ttl:
                return cached[1]

        source = "tronscan"
        items = self._fetch_tronscan(target)
        if items is None:
            log.warning("TronScan не отдал данные по %s - пробую TronGrid", target)
            source = "trongrid"
            items = self._fetch_trongrid(target)
        if items is None:
            log.error("Оба канала не ответили по кошельку %s - платежи не проверены", target)
            self.last_source = ""
            self.last_error = "TronScan and TronGrid did not answer (network or rate limit)"
            return None
        self.last_source = source
        self.last_error = ""
        self._cache[target] = (time.monotonic(), items)
        return items

    # --- обратная совместимость ----------------------------------------
    def check(self, expected_amount: float, wallet: Optional[str] = None,
              since_ts: float = 0.0,
              used_tx: Optional[Set[str]] = None,
              tolerance: float = MATCH_TOLERANCE) -> Optional[str]:
        """tx_id при совпадении суммы, иначе None."""
        if self.mock:
            log.info("[MOCK] оплата %s USDT засчитана", expected_amount)
            return "mock"
        return match_transfer(self.transfers(wallet) or [], expected_amount,
                              since_ts=since_ts, used_tx=used_tx,
                              tolerance=tolerance)
