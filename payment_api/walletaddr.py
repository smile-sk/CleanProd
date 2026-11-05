"""Проверка TRC20-адреса до того, как он попадёт в заказ.

Зачем отдельный модуль: адрес приходит от клиента и уходит в запрос к TronScan.
Мусор в адресе - это либо пустой поход в API (лишний расход нашего лимита
1 запрос/с), либо, при неаккуратной склейке URL, чужая подстановка. Поэтому
адрес проверяем по существу, а не «начинается с T»:

    * длина ровно 34 символа, первый - "T";
    * символы только из base58 (никаких пробелов, кавычек, слэшей, переводов
      строк - их в алфавите base58 просто нет);
    * после декодирования base58 - 25 байт;
    * первый байт 0x41 (версия адреса Tron);
    * последние 4 байта - контрольная сумма sha256(sha256(первые 21 байт)).

Такой адрес нельзя «подделать на глаз»: любая опечатка ломает контрольную
сумму, и мы отвечаем 422 до создания заказа, а не ждём оплату в пустоту.
"""
import hashlib

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
TRON_VERSION = 0x41          # префикс адреса Tron (в base58check это "T")
ADDRESS_LENGTH = 34
RAW_LENGTH = 25              # 1 версия + 20 адрес + 4 контрольная сумма


# Адреса, которые формально проходят base58check, но кошельком НЕ являются.
# 22.10.2026: контракт USDT и адрес сжигания проходили проверку - заказ
# создавался, покупатель платил, деньги исчезали безвозвратно.
BLOCKED_ADDRESSES = {
    "TRX_ADDRESS":
        "this is the USDT TRC20 token contract, not a wallet - funds sent to it "
        "are stuck forever, pass the wallet of your own store",
    "TRX_ADDRESS":
        "this is the USDC TRC20 token contract, not a wallet - funds sent to it "
        "are stuck forever, pass the wallet of your own store",
    "TRX_ADDRESS":
        "this is the TRON burn address - funds sent to it are destroyed, "
        "pass the wallet of your own store",
}


class WalletAddressError(ValueError):
    """Адрес не годится: пустой, не base58, неверная длина, контрольная сумма
    или это вообще не кошелёк, а контракт / адрес сжигания."""


def _b58decode(value: str) -> bytes:
    num = 0
    for char in value:
        index = B58_ALPHABET.find(char)
        if index < 0:
            raise WalletAddressError(
                "wallet must be base58 (only letters and digits, no spaces, "
                "quotes, slashes or line breaks)"
            )
        num = num * 58 + index
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    leading_zeros = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeros + body


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def validate_trc20(value) -> str:
    """Вернуть адрес как есть или поднять WalletAddressError с причиной."""
    if not isinstance(value, str):
        raise WalletAddressError("wallet must be a string")
    address = value.strip()
    if not address:
        raise WalletAddressError("wallet is required")
    if len(address) != ADDRESS_LENGTH:
        raise WalletAddressError(
            "wallet must be {} characters, got {}".format(ADDRESS_LENGTH, len(address))
        )
    if not address.startswith("T"):
        raise WalletAddressError("wallet must start with T (TRC20 address)")
    raw = _b58decode(address)
    if len(raw) != RAW_LENGTH:
        raise WalletAddressError("wallet is not a valid base58check address")
    if raw[0] != TRON_VERSION:
        raise WalletAddressError("wallet is not a TRON address (bad version byte)")
    if _checksum(raw[:21]) != raw[21:]:
        raise WalletAddressError("wallet checksum mismatch - check the address for typos")
    blocked = BLOCKED_ADDRESSES.get(address)
    if blocked:
        raise WalletAddressError(blocked)
    return address
