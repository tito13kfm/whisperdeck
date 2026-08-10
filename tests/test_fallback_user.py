"""Migration fallback user gets a random one-time password (issue #302).

The 'local' user is created only when migrating a pre-multi-user database
(app.py migration block). It used to be created with the static password
'changeme', which sat outside the password policy forever.

Mutation check: test_password_is_random_and_returned_once fails if
get_or_create_fallback_user still hardcodes a password (the changeme
assertion) or returns a bare User (tuple unpack breaks).
"""
from database import User
from services.auth import authenticate_user, get_or_create_fallback_user


class TestFallbackUser:
    def test_password_is_random_and_returned_once(self, db_session):
        user, password = get_or_create_fallback_user(db_session)
        assert user.username == "local"
        assert isinstance(password, str)
        assert len(password) >= 16
        assert password != "changeme"
        # Second call: user exists, no plaintext ever again.
        again, none_password = get_or_create_fallback_user(db_session)
        assert again.id == user.id
        assert none_password is None

    def test_can_login_with_returned_password_only(self, db_session):
        user, password = get_or_create_fallback_user(db_session)
        assert authenticate_user(db_session, "local", password) is not None
        assert authenticate_user(db_session, "local", "changeme") is None

    def test_passwords_differ_across_creations(self, db_session):
        _, first = get_or_create_fallback_user(db_session)
        db_session.query(User).filter(User.username == "local").delete()
        db_session.commit()
        db_session.expunge_all()  # rowid gets reused; drop the stale identity
        _, second = get_or_create_fallback_user(db_session)
        assert first != second
