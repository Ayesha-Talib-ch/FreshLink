"""Phone normalisation and password rules (no database needed)."""

import pytest

from app.domain.validation import check_password_strength, normalize_phone


@pytest.mark.parametrize(
    "raw",
    ["03001234567", "0300 1234567", "0300-1234567", "+92 300 1234567", "923001234567",
     "0092 300 1234567", "(0300) 123-4567", "  +923001234567  "],
)
def test_pakistani_formats_all_become_the_same_number(raw: str) -> None:
    assert normalize_phone(raw) == "+923001234567"


def test_international_number_kept() -> None:
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"


@pytest.mark.parametrize("raw", ["", "12345", "abcdefghijk", "+0123456789", "0300123456789012", "+92-300-12x4567"])
def test_invalid_phone_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="valid phone number"):
        normalize_phone(raw)


def test_good_password_accepted() -> None:
    assert check_password_strength("Correct-Horse-42") == "Correct-Horse-42"


@pytest.mark.parametrize(
    ("password", "message"),
    [
        ("short1!", "at least 8"),
        ("x" * 129, "at most 128"),
        ("password123", "too common"),
        ("PASSWORD123", "too common"),  # case doesn't help
        ("Pakistan123", "too common"),
    ],
)
def test_weak_password_rejected(password: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_password_strength(password)


def test_password_equal_to_email_rejected() -> None:
    with pytest.raises(ValueError, match="email"):
        check_password_strength("Ali@Example.com", email="ali@example.com")


@pytest.mark.parametrize(
    ("amount", "unit", "text"),
    [(15, "package", "15 packages"), (1, "package", "1 package"), (12.5, "kg", "12.5 kg"), (1, "kg", "1 kg"),
     (2.50, "litre", "2.5 litres"), (30, "tray", "30 trays")],
)
def test_format_quantity(amount: float, unit: str, text: str) -> None:
    from app.domain.enums import QuantityUnit
    from app.domain.formatting import format_quantity

    assert format_quantity(amount, QuantityUnit(unit)) == text
