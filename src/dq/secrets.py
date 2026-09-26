"""Reads the Snowflake service user's private key from macOS Keychain.

The key is never written to disk by this module. CLAUDE.md forbids Claude
Code from reading Keychain entries itself in this session -- tests here
mock keyring.get_password with a throwaway in-memory key and never touch
the real entry.
"""

from __future__ import annotations

import base64

import keyring
from cryptography.hazmat.primitives import serialization


class SecretsError(Exception):
    """Raised when the private key can't be loaded from Keychain."""


def load_private_key_der(*, keychain_service: str, keychain_account: str) -> bytes:
    """Load the Snowflake service user's private key from Keychain.

    Returns DER-encoded PKCS8 bytes, ready for snowflake-connector-python's
    `private_key` connection parameter.
    """
    encoded = keyring.get_password(keychain_service, keychain_account)
    if encoded is None:
        raise SecretsError(
            f"No Keychain entry found for service={keychain_service!r}, "
            f"account={keychain_account!r}"
        )

    try:
        pem = base64.b64decode(encoded, validate=True)
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception as exc:
        raise SecretsError(f"Could not decode private key from Keychain: {exc}") from exc

    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
