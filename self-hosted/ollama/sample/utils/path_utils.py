"""
Utility functions for path handling.
"""

import re


def normalize_path(
    path: str,
) -> str:
    """Normalize path, ensuring /items/ prefix.

    Args:
        path: Raw path string

    Returns:
        Normalized path with /items/ prefix
    """
    path = path.strip()
    path = re.sub(r"/+", "/", path)
    if not path.startswith("/items/"):
        path = path.lstrip("/")
        path = f"/items/{path}"
    return path


def extract_name(
    path: str,
) -> str:
    """Extract item name from path.

    Args:
        path: Item path (e.g., /items/my-widget)

    Returns:
        Item name (e.g., my-widget)
    """
    normalized = normalize_path(path)
    return normalized.replace("/items/", "").strip("/")


def validate_name(
    name: str,
) -> bool:
    """Validate item name — lowercase alphanumeric with hyphens only.

    Args:
        name: Item name to validate

    Returns:
        True if valid, False otherwise
    """
    pattern = r"^[a-z0-9]+(-[a-z0-9]+)*$"
    return bool(re.match(pattern, name))
