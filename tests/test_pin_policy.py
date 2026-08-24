"""Regression tests for the shared instructor PIN policy."""

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pin_policy import is_valid_instructor_pin  # noqa: E402


@pytest.mark.parametrize("pin", ("0000", "1234", "0" * 32))
def test_instructor_pin_policy_accepts_ascii_digit_boundaries(pin):
    assert is_valid_instructor_pin(pin) is True


@pytest.mark.parametrize(
    "pin",
    (
        None,
        1234,
        "",
        "123",
        "1" * 33,
        "12a4",
        "12 34",
        " 1234",
        "1234 ",
        "\u0661\u0662\u0663\u0664",
        "\uff11\uff12\uff13\uff14",
    ),
)
def test_instructor_pin_policy_rejects_non_policy_values(pin):
    assert is_valid_instructor_pin(pin) is False
