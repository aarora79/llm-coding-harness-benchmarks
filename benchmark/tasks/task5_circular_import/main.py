"""Entry point — this currently fails with ImportError due to circular imports."""
from services import create_order


def main():
    order = create_order("Alice", "alice@example.com", 99.99)
    print(f"Created: {order}")


if __name__ == "__main__":
    main()
