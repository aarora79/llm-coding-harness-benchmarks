"""A shopping cart implementation to write tests for."""


class ShoppingCart:
    def __init__(self):
        self._items = {}
        self._discount_code = None

    def add_item(self, name: str, price: float, quantity: int = 1):
        if price < 0:
            raise ValueError("Price cannot be negative")
        if quantity < 1:
            raise ValueError("Quantity must be at least 1")
        if name in self._items:
            self._items[name]["quantity"] += quantity
        else:
            self._items[name] = {"price": price, "quantity": quantity}

    def remove_item(self, name: str, quantity: int = None):
        if name not in self._items:
            raise KeyError(f"Item '{name}' not in cart")
        if quantity is None or quantity >= self._items[name]["quantity"]:
            del self._items[name]
        else:
            self._items[name]["quantity"] -= quantity

    def get_total(self) -> float:
        total = sum(
            item["price"] * item["quantity"] for item in self._items.values()
        )
        if self._discount_code == "SAVE10":
            total *= 0.9
        elif self._discount_code == "HALF":
            total *= 0.5
        return round(total, 2)

    def apply_discount(self, code: str):
        valid_codes = {"SAVE10", "HALF"}
        if code not in valid_codes:
            raise ValueError(f"Invalid discount code: {code}")
        self._discount_code = code

    def item_count(self) -> int:
        return sum(item["quantity"] for item in self._items.values())

    def clear(self):
        self._items = {}
        self._discount_code = None

    def get_items(self) -> dict:
        return dict(self._items)
