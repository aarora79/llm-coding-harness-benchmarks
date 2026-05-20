"""Service layer — has circular import with models.py."""
from models import User, Order


def validate_user(user) -> bool:
    return bool(user.name) and "@" in user.email


def create_order(name: str, email: str, amount: float) -> "Order":
    user = User(name=name, email=email)
    if not user.is_valid:
        raise ValueError("Invalid user")
    return Order(user=user, amount=amount)
