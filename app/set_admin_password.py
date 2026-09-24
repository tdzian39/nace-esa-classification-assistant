"""Set the password for the sign-in and for /admin: asks for it twice, writes only its hash.

Run from the app folder:  ../.venv/Scripts/python.exe set_admin_password.py
Both APP_PASSWORD_HASH (the sign-in) and ADMIN_PASSWORD_HASH (the developer page) in
app/.env get the same hash. Nothing but the hash is stored; the password itself is never
written anywhere. Restart the app afterwards.
"""

from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

from core.auth import hash_password

ENV = Path(__file__).resolve().parent / ".env"
NAMES = ("APP_PASSWORD_HASH", "ADMIN_PASSWORD_HASH")


def main() -> int:
    first = getpass.getpass("Nové heslo (nezobrazuje se): ")
    second = getpass.getpass("Ještě jednou: ")
    if not first:
        print("Heslo nesmí být prázdné.", file=sys.stderr)
        return 1
    if first != second:
        print("Hesla se neshodují, zkuste to znovu.", file=sys.stderr)
        return 1
    digest = hash_password(first)
    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    for name in NAMES:
        line = f"{name}='{digest}'"
        if re.search(rf"^{name}=", text, flags=re.MULTILINE):
            text = re.sub(rf"^{name}=.*$", line, text, flags=re.MULTILINE)
        else:
            text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    ENV.write_text(text, encoding="utf-8")
    print(f"Hotovo: hash uložen do {ENV.name} pro přihlášení i pro vývojářskou stránku.")
    print("Heslo samo nikde uloženo není. Teď napište 'done' a aplikace se restartuje.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
