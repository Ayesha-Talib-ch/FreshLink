"""FreshLink command-line tools (run on the server, never exposed as an API).

Usage, from the backend/ folder with the venv active:
    python -m app.cli create-admin

The password is typed at a hidden prompt. It is never passed as a command
argument (that would be saved in the terminal history) and never stored in
the repository.

Output uses plain ASCII only: some Windows terminals (code page 1252) crash
when printing symbols like a check mark.
"""

import argparse
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import create_db_engine, create_session_factory
from app.domain.validation import check_password_strength, normalize_phone
from app.services import auth_service

_email_adapter = TypeAdapter(EmailStr)


def _ask(prompt: str, validate, *, secret: bool = False) -> str:  # noqa: ANN001
    while True:
        raw = getpass.getpass(prompt) if secret else input(prompt)
        try:
            return validate(raw)
        except (ValueError, ValidationError) as exc:
            message = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
            print(f"  [x] {message}")


def _validate_name(raw: str) -> str:
    name = " ".join(raw.split())
    if len(name) < 2:
        raise ValueError("Please enter the full name.")
    return name


def create_admin_command(args: argparse.Namespace) -> int:
    email = args.email or _ask("Admin email: ", lambda v: _email_adapter.validate_python(v.strip()))
    email = _email_adapter.validate_python(email.strip()).lower()
    full_name = _validate_name(args.name) if args.name else _ask("Full name: ", _validate_name)
    phone = normalize_phone(args.phone) if args.phone else _ask("Phone: ", normalize_phone)

    while True:
        password = _ask(
            "Password (hidden): ",
            lambda v: check_password_strength(v, email=email),
            secret=True,
        )
        if getpass.getpass("Repeat password: ") == password:
            break
        print("  [x] The passwords don't match. Try again.")

    settings = get_settings()
    engine = create_db_engine(settings)
    try:
        with create_session_factory(engine)() as db:
            user = auth_service.create_admin(
                db, email=email, phone=phone, full_name=full_name, password=password
            )
    except AppError as exc:
        print(f"Error: {exc.message}")
        return 1
    finally:
        engine.dispose()

    print(f"[OK] Super admin created: {user.email} (id {user.id})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="FreshLink admin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    admin = sub.add_parser("create-admin", help="Create a super administrator account")
    admin.add_argument("--email", help="admin email (asked if omitted)")
    admin.add_argument("--name", help="full name (asked if omitted)")
    admin.add_argument("--phone", help="phone number (asked if omitted)")
    admin.set_defaults(func=create_admin_command)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, ValidationError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
