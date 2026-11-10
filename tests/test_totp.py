import base64

import pytest

from app.totp_service import InvalidSecret, generate, parse_secret


@pytest.mark.parametrize(
    "timestamp,sha1,sha256,sha512",
    [
        (59, "94287082", "46119246", "90693936"),
        (1111111109, "07081804", "68084774", "25091201"),
        (1111111111, "14050471", "67062674", "99943326"),
        (1234567890, "89005924", "91819424", "93441116"),
        (2000000000, "69279037", "90698825", "38618901"),
        (20000000000, "65353130", "77737706", "47863826"),
    ],
)
def test_rfc6238_vectors(timestamp, sha1, sha256, sha512):
    for algorithm, size, expected in (
        ("SHA1", 20, sha1),
        ("SHA256", 32, sha256),
        ("SHA512", 64, sha512),
    ):
        key = (b"1234567890" * 7)[:size]
        secret = base64.b32encode(key).decode().rstrip("=")
        config = parse_secret(f"otpauth://totp/test?secret={secret}&algorithm={algorithm}&digits=8")
        code, expiry = generate(config, timestamp)
        assert code == expected
        assert expiry == (timestamp // 30 + 1) * 30


def test_raw_defaults_and_spaces():
    config = parse_secret(" jbsw y3dp ehpk 3pxp ")
    assert config.secret == "JBSWY3DPEHPK3PXP"
    assert (config.algorithm, config.digits, config.period) == ("SHA1", 6, 30)
    assert config.secret not in repr(config)


def test_uri_parameters_are_preserved_and_labels_discarded():
    config = parse_secret(
        "otpauth://totp/Private:label?secret=JBSWY3DPEHPK3PXP"
        "&issuer=Private&algorithm=SHA256&digits=8&period=60"
    )
    assert (config.algorithm, config.digits, config.period) == ("SHA256", 8, 60)
    assert "Private" not in repr(config)


def test_boundaries_same_window_and_six_digit_leading_zero():
    config = parse_secret("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    assert generate(config, 1111111109)[0] == "081804"
    assert generate(config, 30)[0] == generate(config, 59.99)[0]
    assert generate(config, 29.99)[0] != generate(config, 30)[0]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "bad-secret!",
        "AAAA",
        "a" * 3000,
        "otpauth://hotp/test?secret=JBSWY3DPEHPK3PXP&counter=0",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&algorithm=MD5",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&digits=7",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&period=0",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&period=90",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&secret=OTHER",
        "otpauth://totp/test?issuer=MissingSecret",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP&counter=1",
        "otpauth://totp/test?secret=JBSWY3DPEHPK3PXP#fragment",
    ],
)
def test_invalid_input_never_echoed(raw):
    with pytest.raises(InvalidSecret) as exc:
        parse_secret(raw)
    assert str(exc.value) == "Invalid or unsupported TOTP secret."
