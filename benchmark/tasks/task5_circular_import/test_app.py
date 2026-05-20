"""Tests that must pass after fixing the circular import."""


def test_import_main():
    import main  # noqa: F401


def test_create_order():
    from services import create_order
    order = create_order("Bob", "bob@test.com", 50.0)
    assert order.amount == 50.0
    assert order.user.name == "Bob"


def test_invalid_user():
    import pytest
    from services import create_order
    with pytest.raises(ValueError):
        create_order("", "no-email", 10.0)


def test_user_model():
    from models import User
    u = User("Charlie", "charlie@x.com")
    assert u.is_valid is True


def test_user_invalid_email():
    from models import User
    u = User("Dave", "not-an-email")
    assert u.is_valid is False
