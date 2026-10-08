import base64
import binascii


def encode_canonical_base64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode_canonical_base64(value: str) -> bytes:
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as error:
        raise ValueError("The encoded credential value is invalid.") from error
    if base64.b64encode(decoded) != encoded:
        raise ValueError("The encoded credential value is not canonical.")
    return decoded
