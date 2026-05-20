"""Data models — has circular import with services.py."""
from services import validate_user


class User:
    def __init__(self, name: str, email: str):
        self.name = name
        self.email = email
        self.is_valid = validate_user(self)

    def __repr__(self):
        return f"User(name={self.name!r}, email={self.email!r})"


class Order:
    def __init__(self, user: User, amount: float):
        self.user = user
        self.amount = amount

    def __repr__(self):
        return f"Order(user={self.user.name!r}, amount={self.amount})"
