"""
Data validation utilities.
"""

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class DataValidator:
    """Validates data payloads against an optional schema."""

    def __init__(
        self,
        schema: Optional[dict] = None,
        strict: bool = False,
    ):
        self.schema = schema
        self.strict = strict

    def validate(
        self,
        data: dict,
        field: Optional[str] = None,
    ) -> Optional[bool]:
        """Validate a data payload.

        Args:
            data: The data dict to validate
            field: Optional specific field to check

        Returns:
            True if valid, False if invalid, None if no data
        """
        if not data:
            return None
        if field is not None:
            return field in data
        return True

    def get_schema(self) -> Optional[dict]:
        """Return the current schema."""
        return self.schema
