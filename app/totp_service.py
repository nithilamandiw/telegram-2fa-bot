"""Pure TOTP input validation and generation. Exceptions never quote input."""

import base64
import binascii
import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

import pyotp

MAX_INPUT_LENGTH = 2048
ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class InvalidSecret(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid or unsupported TOTP secret.")


@dataclass(frozen=True, repr=False)
class TOTPConfig:
    secret: str = field(repr=False)
    algorithm: str = "SHA1"
    digits: int = 6
    period: int = 30


def parse_secret(raw: str) -> TOTPConfig:
    """Accept Base32 or an ordinary TOTP URI; discard URI account labels."""
    if not raw or len(raw) > MAX_INPUT_LENGTH:
        raise InvalidSecret()
    value = raw.strip()
    algorithm, digits, period = "SHA1", 6, 30
    try:
        if value.lower().startswith("otpauth:"):
            uri = urlsplit(value)
            if uri.scheme != "otpauth" or uri.netloc != "totp" or not uri.path.strip("/"):
                raise InvalidSecret()
            if uri.fragment:
                raise InvalidSecret()
            pairs = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
            params = dict(pairs)
            if len(params) != len(pairs):
                raise InvalidSecret()
            if set(params) - {"secret", "algorithm", "digits", "period", "issuer"}:
                raise InvalidSecret()
            value = params["secret"]
            algorithm = params.get("algorithm", "SHA1").upper()
            digits = int(params.get("digits", "6"))
            period = int(params.get("period", "30"))
        if algorithm not in ALGORITHMS or digits not in (6, 8) or period not in (30, 60):
            raise InvalidSecret()
        secret = re.sub(r"\s+", "", value).upper().rstrip("=")
        if not re.fullmatch(r"[A-Z2-7]+", secret):
            raise InvalidSecret()
        decoded = base64.b32decode(secret + "=" * (-len(secret) % 8))
        if len(decoded) < 10 or len(decoded) > 128:
            raise InvalidSecret()
        # Reject non-canonical trailing bits rather than silently changing the key.
        if base64.b32encode(decoded).decode().rstrip("=") != secret:
            raise InvalidSecret()
        return TOTPConfig(secret, algorithm, digits, period)
    except (ValueError, KeyError, TypeError, binascii.Error):
        raise InvalidSecret() from None


def generate(config: TOTPConfig, unix_time: float) -> tuple[str, int]:
    totp = pyotp.TOTP(
        config.secret,
        digits=config.digits,
        interval=config.period,
        digest=ALGORITHMS[config.algorithm],
    )
    expires_at = (int(unix_time) // config.period + 1) * config.period
    return totp.at(unix_time), expires_at
