import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dq.secrets import SecretsError, load_private_key_der


def _make_test_key_b64() -> tuple[bytes, str]:
    """A throwaway in-memory RSA key, base64-encoded like Keychain stores it.
    Never touches the real Keychain or disk."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem, base64.b64encode(pem).decode()


def test_loads_der_bytes_from_mocked_keychain_entry(monkeypatch):
    pem, encoded = _make_test_key_b64()

    monkeypatch.setattr("dq.secrets.keyring.get_password", lambda service, account: encoded)

    der = load_private_key_der(keychain_service="dq-snowflake-key", keychain_account="dq_poc")

    # Round-trip: the DER bytes should load back to the same key.
    original = serialization.load_pem_private_key(pem, password=None)
    reloaded = serialization.load_der_private_key(der, password=None)
    assert reloaded.private_numbers() == original.private_numbers()


def test_passes_service_and_account_through(monkeypatch):
    _, encoded = _make_test_key_b64()
    seen = {}

    def fake_get_password(service, account):
        seen["service"] = service
        seen["account"] = account
        return encoded

    monkeypatch.setattr("dq.secrets.keyring.get_password", fake_get_password)

    load_private_key_der(keychain_service="dq-snowflake-key", keychain_account="dq_poc")

    assert seen == {"service": "dq-snowflake-key", "account": "dq_poc"}


def test_missing_keychain_entry_raises(monkeypatch):
    monkeypatch.setattr("dq.secrets.keyring.get_password", lambda service, account: None)

    with pytest.raises(SecretsError, match="No Keychain entry"):
        load_private_key_der(keychain_service="dq-snowflake-key", keychain_account="dq_poc")


def test_malformed_base64_raises(monkeypatch):
    monkeypatch.setattr(
        "dq.secrets.keyring.get_password", lambda service, account: "not-valid-base64!!"
    )

    with pytest.raises(SecretsError):
        load_private_key_der(keychain_service="dq-snowflake-key", keychain_account="dq_poc")


def test_valid_base64_but_not_a_key_raises(monkeypatch):
    junk = base64.b64encode(b"this is not a PEM key").decode()
    monkeypatch.setattr("dq.secrets.keyring.get_password", lambda service, account: junk)

    with pytest.raises(SecretsError):
        load_private_key_der(keychain_service="dq-snowflake-key", keychain_account="dq_poc")
