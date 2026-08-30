import base64

import pytest

from services.security import decrypt_api_key, encrypt_api_key
from services.settings import _decrypt_key_if_needed


def test_encrypt_decrypt_roundtrip():
    secret = "test-session-secret-xyz"
    plaintext = "sk-test-12345-abcdef"
    encrypted = encrypt_api_key(plaintext, secret)
    assert encrypted != plaintext
    assert len(encrypted) >= 64
    assert decrypt_api_key(encrypted, secret) == plaintext


def test_encrypt_different_secrets_produce_different_ciphertexts():
    plaintext = "sk-test-same-key"
    c1 = encrypt_api_key(plaintext, "secret-one")
    c2 = encrypt_api_key(plaintext, "secret-two")
    assert c1 != c2


def test_encrypt_same_secret_produces_different_ciphertexts_due_to_random_salt():
    plaintext = "sk-test-same-key"
    secret = "same-secret"
    c1 = encrypt_api_key(plaintext, secret)
    c2 = encrypt_api_key(plaintext, secret)
    assert c1 != c2
    assert decrypt_api_key(c1, secret) == plaintext
    assert decrypt_api_key(c2, secret) == plaintext


def test_encrypt_ciphertext_is_valid_base64():
    encrypted = encrypt_api_key("sk-hello", "my-secret")
    raw = base64.b64decode(encrypted.encode("utf-8"))
    assert len(raw) > 16


def test_decrypt_with_wrong_secret_raises():
    encrypted = encrypt_api_key("sk-hello", "correct-secret")
    with pytest.raises(Exception):
        decrypt_api_key(encrypted, "wrong-secret")


def test_decrypt_key_if_needed_plaintext_passthrough_short():
    assert _decrypt_key_if_needed("sk-short", "any-secret") == "sk-short"
    assert _decrypt_key_if_needed("gsk_abc123", "any-secret") == "gsk_abc123"


def test_decrypt_key_if_needed_encrypted_roundtrip():
    secret = "roundtrip-secret"
    plaintext = "sk-my-api-key-999"
    encrypted = encrypt_api_key(plaintext, secret)
    assert _decrypt_key_if_needed(encrypted, secret) == plaintext


def test_decrypt_key_if_needed_corrupted_ciphertext_returns_empty():
    secret = "my-secret"
    plaintext = "sk-test-key-abc"
    encrypted = encrypt_api_key(plaintext, secret)
    corrupted = encrypted[:-4] + "XXXX"
    assert _decrypt_key_if_needed(corrupted, secret) == ""


def test_decrypt_key_if_needed_empty_inputs():
    assert _decrypt_key_if_needed("", "secret") == ""
    assert _decrypt_key_if_needed("", "") == ""


def test_decrypt_key_if_needed_no_secret_long_ciphertext_returns_empty():
    pt = "sk-will-be-encrypted"
    enc = encrypt_api_key(pt, "some-secret")
    assert _decrypt_key_if_needed(enc, "") == ""
    assert _decrypt_key_if_needed(enc, None) == ""  # type: ignore[arg-type]


def test_decrypt_key_if_needed_long_plaintext_passthrough():
    long_plain = "sk-or-v1-" + "a" * 64
    assert _decrypt_key_if_needed(long_plain, "wrong-secret") == long_plain
    assert _decrypt_key_if_needed(long_plain, "") == long_plain
    prefix_plain = "sk-proj-" + "b" * 120
    assert _decrypt_key_if_needed(prefix_plain, "any-secret") == prefix_plain
    valid_b64_plain = base64.b64encode(b"x" * 48).decode()
    assert _decrypt_key_if_needed(valid_b64_plain, "secret") == valid_b64_plain
