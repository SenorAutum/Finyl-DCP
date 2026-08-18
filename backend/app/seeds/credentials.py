"""AUTH-03 — seed credential handling.

Passwords are NEVER printed to stdout. Behaviour:

* If ``SEED_DEFAULT_PASSWORD`` is set in the environment, that value is used for
  every seeded user (useful for controlled demo environments).
* Otherwise a strong random password is generated per user.

Either way the plaintext credentials are written to a gitignored file
(``backend/storage/seed_credentials.txt``) so an operator can distribute them
securely out-of-band. All seeded users are created with
``force_password_reset=True`` so the temporary password must be changed on first
login and is useless to anyone who never receives the file.
"""
import os
import secrets
from datetime import datetime
from pathlib import Path

_ENV_PASSWORD = os.environ.get("SEED_DEFAULT_PASSWORD")
# backend/app/seeds/credentials.py -> parents[2] == backend/
_CRED_PATH = Path(__file__).resolve().parents[2] / "storage" / "seed_credentials.txt"


class SeedCredentials:
    """Collects (email, password) pairs and flushes them to the gitignored file."""

    def __init__(self):
        self._entries: list[tuple[str, str]] = []

    def password_for(self, email: str) -> str:
        pw = _ENV_PASSWORD or secrets.token_urlsafe(12)
        self._entries.append((email, pw))
        return pw

    def flush(self, source: str) -> str:
        """Append the collected credentials to the file. Returns a safe summary
        string (counts only — never the passwords)."""
        if not self._entries:
            return "no seed credentials generated"
        _CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        origin = "SEED_DEFAULT_PASSWORD env" if _ENV_PASSWORD else "generated random per-user"
        with _CRED_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"\n# {source} @ {stamp} ({origin}) — "
                     f"all users have force_password_reset=True\n")
            for email, pw in self._entries:
                fh.write(f"{email}\t{pw}\n")
        try:
            os.chmod(_CRED_PATH, 0o600)
        except OSError:
            pass
        n = len(self._entries)
        self._entries.clear()
        return f"{n} credential(s) written to {_CRED_PATH} (chmod 600)"
