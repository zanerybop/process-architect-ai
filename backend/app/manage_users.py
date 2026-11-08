"""Local account provisioning. Passwords are never accepted as command arguments."""

import argparse
import getpass
import sqlite3
import sys

from pydantic import ValidationError

from backend.app.auth import AuthError, AuthStore
from backend.app.config import Settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Управление локальными аккаунтами Process Architect.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Создать аккаунт без изменения существующих.")
    create.add_argument("username", help="Логин нового аккаунта.")
    create.add_argument(
        "--password-stdin", action="store_true", help="Прочитать пароль одной строкой из stdin.",
    )
    args = parser.parse_args(argv)
    try:
        password = (
            sys.stdin.readline(131).removesuffix("\n").removesuffix("\r")
            if args.password_stdin else getpass.getpass("Пароль: ")
        )
        user = AuthStore(Settings().data_dir).create_user(args.username, password)
    except AuthError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error, ValidationError):
        print("ACCOUNT_CREATE_FAILED: Не удалось создать аккаунт. Проверьте настройки и доступ к хранилищу.",
              file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("ACCOUNT_CREATE_CANCELLED: Создание аккаунта отменено.", file=sys.stderr)
        return 130
    print(user["username"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
