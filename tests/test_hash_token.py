"""_hash_token (services/auth.py, issue #406 dedup): one SHA-256 hex wrapper
replacing hash_reset_token/hash_device_token/hash_invite_token (byte-identical,
now deleted).

The existing tests that exercise hashing (test_auth_admin.py,
test_device_token.py, test_registration_gate.py) all assert
`stored_hash == _hash_token(plaintext)` — comparing the app's output against
the *same function's* own output. That's vacuous under mutation: if
_hash_token were mutated to `return ""`, the app would store "" and the test
would compute "" too, so it would still pass. It asserts "the app hashes the
way I hash", never "the app hashes correctly".

This test instead pins a hardcoded expected digest (computed independently,
not via _hash_token) and checks the output shape, so it cannot pass by
mirroring a broken implementation.
"""
from services.auth import _hash_token


def test_hash_token_matches_known_sha256_digest():
    # hashlib.sha256(b"abc").hexdigest(), computed independently of the
    # function under test.
    assert _hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_hash_token_output_is_not_plaintext_and_is_64_lowercase_hex():
    token = "abc"
    digest = _hash_token(token)
    assert digest != token
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)
