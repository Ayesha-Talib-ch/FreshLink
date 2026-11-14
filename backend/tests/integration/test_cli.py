"""The create-admin command (typed answers are simulated)."""

from collections.abc import Iterator

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cli
from app.core.config import Settings
from app.domain.enums import UserRole, VerificationStatus
from app.models.user import User
from tests.conftest import MakeUser


@pytest.fixture
def use_test_db(monkeypatch: pytest.MonkeyPatch, test_settings: Settings) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: test_settings)


def _feed(monkeypatch: pytest.MonkeyPatch, *, inputs: list[str], secrets: list[str]) -> None:
    typed: Iterator[str] = iter(inputs)
    hidden: Iterator[str] = iter(secrets)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(hidden))


@pytest.mark.usefixtures("use_test_db")
def test_create_admin_interactively(monkeypatch: pytest.MonkeyPatch, db_session: Session, capsys) -> None:  # noqa: ANN001
    _feed(
        monkeypatch,
        inputs=["not-an-email", "Admin@FreshLink.pk", "Ayesha Admin", "0300 9998887"],
        # weak password -> asked again; mismatch -> asked again; then OK
        secrets=["password123", "Admin-Strong-77", "different", "Admin-Strong-77", "Admin-Strong-77"],
    )
    assert cli.main(["create-admin"]) == 0

    admin = db_session.scalar(select(User))
    assert admin.email == "admin@freshlink.pk"
    assert admin.phone == "+923009998887"
    assert admin.role == UserRole.ADMIN
    assert admin.is_super_admin is True
    assert admin.verification_status == VerificationStatus.APPROVED
    assert admin.password_hash.startswith("$argon2id$")

    output = capsys.readouterr().out
    assert "too common" in output and "don't match" in output
    assert "Admin-Strong-77" not in output  # the password is never printed


@pytest.mark.usefixtures("use_test_db")
def test_create_admin_duplicate_email_fails(monkeypatch: pytest.MonkeyPatch, make_user: MakeUser, capsys) -> None:  # noqa: ANN001
    make_user(email="taken@example.com")
    _feed(monkeypatch, inputs=[], secrets=["Admin-Strong-77", "Admin-Strong-77"])
    code = cli.main(["create-admin", "--email", "taken@example.com", "--name", "X Y", "--phone", "03001112223"])
    assert code == 1
    assert "already exists" in capsys.readouterr().out


@pytest.mark.usefixtures("use_test_db")
def test_create_admin_invalid_phone_argument(monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    code = cli.main(["create-admin", "--email", "a@example.com", "--name", "X Y", "--phone", "123"])
    assert code == 1
    assert "valid phone number" in capsys.readouterr().out


def test_create_admin_works_in_a_cp1252_windows_terminal(test_settings: Settings, db_session: Session) -> None:
    """Regression: printing a check-mark symbol crashed in terminals using code page 1252."""
    import os
    import subprocess
    import sys

    script = (
        "import builtins, sys\n"
        "from app import cli\n"
        "typed = iter(['cp1252.admin@example.com', 'Code Page Admin', '0300 1110777'])\n"
        "hidden = iter(['password123', 'Admin-Fresh-2026', 'Admin-Fresh-2026'])\n"
        "builtins.input = lambda p='': next(typed)\n"
        "cli.getpass.getpass = lambda p='': next(hidden)\n"
        "sys.exit(cli.main(['create-admin']))\n"
    )
    env = {**os.environ, "PYTHONIOENCODING": "cp1252",
           "DATABASE_URL": test_settings.database_url.get_secret_value()}
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            encoding="cp1252", env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[x] This password is too common" in result.stdout
    assert "[OK] Super admin created: cp1252.admin@example.com" in result.stdout
    assert db_session.scalar(select(User).where(User.email == "cp1252.admin@example.com")) is not None
