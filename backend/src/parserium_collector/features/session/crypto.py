import base64
import hashlib
import hmac
import secrets


def generate_pairing_code() -> str:
    return secrets.token_urlsafe(18)


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def keyed_digest(secret: bytes, purpose: str, value: str) -> str:
    message = f"{purpose}\0{value}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def csrf_token(secret: bytes, session_token: str) -> str:
    digest = hmac.new(secret, f"csrf\0{session_token}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
