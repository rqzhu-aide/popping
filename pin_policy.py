"""Shared instructor PIN validation policy."""

import re


_INSTRUCTOR_PIN_PATTERN = re.compile(r"[0-9]{4,32}")


def is_valid_instructor_pin(value):
    """Return whether value is exactly 4-32 ASCII digits."""
    return (
        isinstance(value, str)
        and _INSTRUCTOR_PIN_PATTERN.fullmatch(value) is not None
    )
