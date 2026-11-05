"""
Токены доступа и авторизация пользователей.

Единый источник истины - файлы в data/ (gitignored):
    tokens.txt            - все выпущенные токены
    used_tokens.txt       - токены, которыми уже активировали доступ
    authorized_users.txt  - chat_id пользователей с активным доступом

Раньше состояние дублировалось в трёх местах (processed_payments.txt +
database.json + tokens.txt). Теперь: заказы - в orders.json (см. pay_bot),
токены/доступ - здесь.
"""
import uuid

import config


def _read(path) -> set:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _append(path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def issue_token() -> str:
    """Выпустить новый токен и сразу записать в tokens.txt."""
    token = str(uuid.uuid4())
    _append(config.TOKENS_FILE, token)
    return token


def valid_tokens() -> set:
    return _read(config.TOKENS_FILE)


def used_tokens() -> set:
    return _read(config.USED_TOKENS_FILE)


def authorized_users() -> set:
    return _read(config.AUTHORIZED_USERS_FILE)


def is_authorized(user_id) -> bool:
    return str(user_id) in authorized_users()


def blocked_users() -> set:
    """Пользователи, которые заблокировали бота (доступ у них СОХРАНЁН,
    просто рассылка им приостановлена, чтобы не долбить в стену)."""
    return _read(config.BLOCKED_USERS_FILE)


def mark_blocked(user_id) -> None:
    uid = str(user_id)
    if uid not in blocked_users():
        _append(config.BLOCKED_USERS_FILE, uid)


def unblock(user_id) -> None:
    """Снять флаг - пользователь снова доступен (написал боту)."""
    uid = str(user_id)
    current = blocked_users()
    if uid in current:
        current.discard(uid)
        config.BLOCKED_USERS_FILE.write_text(
            "".join(line + "\n" for line in sorted(current)), encoding="utf-8"
        )


def consume_token(token: str, user_id) -> bool:
    """Активировать доступ по токену. True - токен валиден и ещё не использован."""
    if token in valid_tokens() and token not in used_tokens():
        _append(config.USED_TOKENS_FILE, token)
        _append(config.AUTHORIZED_USERS_FILE, str(user_id))
        return True
    return False
