"""Authenticated encryption bound to the tenant and the connection id."""

import base64
import binascii
import hashlib
import hmac
import json
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app.llm.base import ModelError
from app.settings import settings


def master():
    value = settings().chatmoney_credential_encryption_key
    try:
        key = (
            base64.b64decode(value.get_secret_value(), altchars=b"-_", validate=True)
            if value
            else b""
        )
        if len(key) != 32:
            raise ValueError
        return key
    except (ValueError, binascii.Error):
        raise ModelError("MODEL_ENCRYPTION") from None


def context(tenant, connection_id, owner=None):
    fields = (
        ["chatmoney:model:v1", tenant, connection_id]
        if owner is None
        else ["chatmoney:model:v2", tenant, connection_id, owner]
    )
    return json.dumps(fields, separators=(",", ":")).encode()


def seal(tenant, connection_id, owner, value):
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(master()).encrypt(
        nonce, value.encode(), context(tenant, connection_id, owner)
    )
    return "v2." + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def unseal(connection):
    try:
        stored = connection.encrypted_api_key
        # Existing ciphertext stays readable by its original creator after the permission migration.
        if stored.startswith("v2."):
            aad = context(connection.tenant, connection.id, connection.created_by)
            stored = stored[3:]
        else:
            aad = context(connection.tenant, connection.id)
        blob = base64.urlsafe_b64decode(stored)
        return AESGCM(master()).decrypt(blob[:12], blob[12:], aad).decode()
    except (InvalidTag, ValueError, TypeError):
        raise ModelError("MODEL_ENCRYPTION") from None


def fingerprint(value):
    return hmac.new(
        master(), b"idempotency:v1\0" + value.encode(), hashlib.sha256
    ).hexdigest()
