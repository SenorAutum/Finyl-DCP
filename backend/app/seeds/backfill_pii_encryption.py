"""
PII-02 — one-off, idempotent backfill for at-rest national_id encryption.

Encrypts any plaintext ``national_id`` values already in the live DB and populates
the ``borrowers.national_id_hash`` blind index. Covers two tables:

  * borrowers          — national_id (encrypt in place) + national_id_hash
  * client_next_of_kin — national_id (encrypt in place)

Idempotent & safe to re-run:
  * A value already stored as an ``enc:v1:`` token is skipped (never re-encrypted).
  * ``national_id_hash`` is (re)derived from the decrypted plaintext every run, so
    a partial/interrupted run is simply completed on the next run.

Runs raw SQL on purpose (bypassing the ORM's transparent EncryptedText) so it can
inspect the *stored* form and act only on rows that still need work.

NEVER prints PII or key material — only aggregate counts.

Usage:
    cd backend && python -m app.seeds.backfill_pii_encryption
"""
from sqlalchemy import text

from app.core.crypto import encrypt_pii, decrypt_pii, pii_hash, _PREFIX
from app.core.database import SessionLocal
from app.core.config import settings


def _backfill_borrowers(db) -> dict:
    rows = db.execute(text(
        "SELECT id, national_id, national_id_hash FROM borrowers"
    )).fetchall()
    encrypted = 0
    hashed = 0
    for row in rows:
        rid, stored, stored_hash = row.id, row.national_id, row.national_id_hash
        if stored is None or stored == "":
            continue
        # Plaintext is the decrypted view regardless of current stored form.
        plaintext = decrypt_pii(stored)
        new_values = {}
        if not str(stored).startswith(_PREFIX):
            # still plaintext at rest -> encrypt in place
            new_values["national_id"] = encrypt_pii(plaintext)
            encrypted += 1
        want_hash = pii_hash(plaintext)
        if stored_hash != want_hash:
            new_values["national_id_hash"] = want_hash
            hashed += 1
        if new_values:
            sets = ", ".join(f"{k} = :{k}" for k in new_values)
            db.execute(text(f"UPDATE borrowers SET {sets} WHERE id = :id"),
                       {**new_values, "id": rid})
    return {"scanned": len(rows), "encrypted": encrypted, "hash_set": hashed}


def _backfill_next_of_kin(db) -> dict:
    rows = db.execute(text(
        "SELECT id, national_id FROM client_next_of_kin"
    )).fetchall()
    encrypted = 0
    for row in rows:
        rid, stored = row.id, row.national_id
        if stored is None or stored == "":
            continue
        if str(stored).startswith(_PREFIX):
            continue  # already encrypted
        db.execute(text("UPDATE client_next_of_kin SET national_id = :v WHERE id = :id"),
                   {"v": encrypt_pii(decrypt_pii(stored)), "id": rid})
        encrypted += 1
    return {"scanned": len(rows), "encrypted": encrypted}


def main() -> None:
    db = SessionLocal()
    try:
        # Ensure raw SQL below resolves unqualified table names to the app schema.
        db.execute(text(f"SET search_path TO {settings.DB_SCHEMA}, public"))
        b = _backfill_borrowers(db)
        n = _backfill_next_of_kin(db)
        db.commit()
    finally:
        db.close()

    print("PII-02 backfill complete (no PII/keys printed):")
    print(f"  borrowers          : scanned={b['scanned']} "
          f"national_id_encrypted={b['encrypted']} national_id_hash_set={b['hash_set']}")
    print(f"  client_next_of_kin : scanned={n['scanned']} "
          f"national_id_encrypted={n['encrypted']}")


if __name__ == "__main__":
    main()
