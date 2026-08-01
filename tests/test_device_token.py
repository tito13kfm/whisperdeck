from sqlalchemy import inspect


def test_users_table_has_device_token_columns(db_session):
    inspector = inspect(db_session.get_bind())
    columns = {c["name"] for c in inspector.get_columns("users")}
    assert "local_device_token_hash" in columns
    assert "local_device_token_created_at" in columns
