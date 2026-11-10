"""端末に結びつける鍵と、別の端末へ移る引き継ぎコード。ログインの代わり。

URLは画面共有や履歴、ログに残りやすいので、URLだけでは開けないようにする。
鍵とコードはハッシュだけを保存する。
"""
import hashlib
import secrets

COOKIE_NAME = "dh_device"
COOKIE_MAX_AGE_S = 400 * 24 * 3600  # ブラウザが認める最長
MAX_DEVICES = 5  # 1人が使える端末の数。超えたら古い端末から外す

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 見間違えやすい I, L, O, 0, 1 は使わない
_CODE_LENGTH = 12
_CODE_GROUP = 4


def new_key() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_transfer_code() -> str:
    """画面に出す引き継ぎコード。読みやすいよう4文字ごとに区切る。"""
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    return "-".join(code[i:i + _CODE_GROUP] for i in range(0, _CODE_LENGTH, _CODE_GROUP))


def normalize_code(text: str) -> str:
    """打ち間違えやすい区切りや空白、大文字小文字の違いをなくす。"""
    return "".join(ch for ch in text.upper() if ch.isalnum())


def hash_code(text: str) -> str:
    return hash_secret(normalize_code(text))
