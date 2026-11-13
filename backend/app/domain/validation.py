"""Input normalisation rules shared by the API and the create-admin command."""

import re

# Small list of very common passwords that are rejected outright.
# (A real deployment could use a larger list; this blocks the obvious ones.)
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "12345678", "123456789",
        "1234567890", "11111111", "00000000", "12341234", "87654321", "qwerty123",
        "qwertyuiop", "asdfghjkl", "iloveyou", "admin123", "administrator", "welcome1",
        "welcome123", "letmein1", "abc12345", "abcd1234", "pakistan", "pakistan123",
        "freshlink", "freshlink123", "football", "baseball", "sunshine", "princess",
    }
)

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def check_password_strength(password: str, *, email: str | None = None) -> str:
    """Return the password unchanged, or raise ValueError with a simple message."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters long.")
    if password.lower() in COMMON_PASSWORDS:
        raise ValueError("This password is too common. Please choose a less predictable one.")
    if email and password.lower() == email.lower():
        raise ValueError("Password must not be the same as your email address.")
    return password


_PHONE_SEPARATORS = re.compile(r"[\s\-().]")


def normalize_phone(raw: str) -> str:
    """Store every phone number in one international format (E.164), e.g. +923001234567.

    Without this, "0300-1234567" and "+92 300 1234567" would be saved as two
    different numbers and the UNIQUE rule on phone would not catch duplicates.
    Pakistani local formats are converted automatically (deployment is in Pakistan).
    """
    phone = _PHONE_SEPARATORS.sub("", raw.strip())
    if phone.startswith("00"):  # 0092300... -> +92300...
        phone = "+" + phone[2:]
    elif phone.startswith("0") and len(phone) == 11:  # 03001234567 (Pakistan mobile)
        phone = "+92" + phone[1:]
    elif phone.startswith("92") and len(phone) == 12:  # 923001234567
        phone = "+" + phone

    if not re.fullmatch(r"\+[1-9]\d{9,14}", phone):
        raise ValueError(
            "Enter a valid phone number, e.g. 0300 1234567 or +92 300 1234567."
        )
    return phone
