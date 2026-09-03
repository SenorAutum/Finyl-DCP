# Finyl-DCP — Running Independently (off Abacus)

This runbook explains how to run Finyl-DCP on your own infrastructure, fully
independent of the Abacus platform, and how to migrate the existing live data
across. The application is deliberately **host-agnostic**: all runtime
configuration comes from environment variables (see `.env.example`), so the same
code and containers run on Abacus, a plain VPS, AWS/GCP, etc. — only the env
differs.

> The live Abacus deployment (`https://finyl-dcp.abacusai.cloud`) keeps working
> exactly as before. Everything here is additive; nothing in this document is
> required to keep the Abacus instance running.

---

## 1. Architecture — what runs where

Finyl-DCP has three moving parts:

| Component | What it is | Notes |
|-----------|------------|-------|
| **Backend API** | FastAPI (Python 3.12) + Uvicorn | Serves `/api/*`. Stateless except for uploaded documents on disk (`STORAGE_DIR`). |
| **Frontend SPA** | React built with Vite → static files | Talks to the backend. Served same-origin behind nginx (default) or hosted separately (e.g. Netlify). |
| **Database** | PostgreSQL 14+ | Holds all data. PII columns (e.g. `national_id`) are encrypted at rest with `FIELD_ENCRYPTION_KEY`. |

Two supported topologies:

- **Option A (recommended): single host + Docker Compose.** One VPS runs
  Postgres + backend + frontend behind one domain. The SPA is served
  same-origin, so no CORS is involved. Simplest to operate.
- **Option B: split hosting.** Frontend on a static host (Netlify), backend +
  Postgres on a VPS or managed services. Requires CORS (`ALLOWED_ORIGINS`) and
  a build-time `VITE_API_URL`.

Already-portable assets in the repo (no changes needed to reuse them):
`docker-compose.yml`, `backend/Dockerfile`, `frontend/Dockerfile`,
`frontend/nginx.conf`.

---

## 2. Option A — single VPS with Docker Compose (recommended)

Prerequisites: a Linux host with Docker + Docker Compose, a domain name, and
ports 80/443 open.

### 2.1 Clone and configure

```bash
git clone https://github.com/SenorAutum/Finyl-DCP.git
cd Finyl-DCP
cp .env.example .env
```

Edit `.env` and fill in **at least** these before first boot:

- `JWT_SECRET` — REQUIRED. The app refuses to boot with the placeholder or a
  value shorter than 32 chars. Generate one:
  ```bash
  python3 -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- `FIELD_ENCRYPTION_KEY` — the PRIMARY key that encrypts PII at rest. Generate:
  ```bash
  python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  > **CRITICAL — migrating existing data?** Do **NOT** generate a new key. You
  > MUST reuse the exact `FIELD_ENCRYPTION_KEY` (and any `PII_ENCRYPTION_KEY`)
  > that encrypted the current data, or existing `national_id` values become
  > permanently undecryptable. See section 4. Only generate a fresh key for a
  > brand-new install with no data to carry over.
- `LLM_BASE_URL` / `LLM_API_KEY` — for the AI agent + vision OCR (optional; the
  rest of the app runs without it).
- Integration credentials as needed (`DARAJA_*`, `UWAZII_*`, `CRB_*`, `EKYC_*`).
  All are credential-gated — blank means "not configured", never a fake success.
- `DARAJA_CALLBACK_BASE_URL` — set to your public backend URL (see cutover, §5).

`docker-compose.yml` overrides `DATABASE_URL`, `DB_SCHEMA=public` and
`STORAGE_DIR` for the bundled Postgres automatically, so you can leave those at
their defaults in `.env`.

### 2.2 Bring the stack up

```bash
docker compose up --build -d
```

This starts Postgres (with a persistent `pgdata` volume), the backend on
`:8000`, and the nginx-served frontend on `:8080`.

### 2.3 Initialise the database

**Fresh install (new, empty database):** create the current schema from the
models, then seed. `AUTO_CREATE_TABLES=true` makes the backend build the full
current schema on boot (the models are the source of truth for a new DB; the
numbered `backend/migrations/*.sql` are incremental upgrades for *existing*
databases, not needed for a fresh one):

```bash
# One-time: let the app create tables on first boot
docker compose exec backend env AUTO_CREATE_TABLES=true python -c "from app.core.database import Base, engine, ensure_schema; import app.models; ensure_schema(); Base.metadata.create_all(bind=engine); print('schema created')"

# Seed demo data + RBAC users/thresholds (optional but recommended for a new install)
docker compose exec backend python -m app.seeds.seed --force
docker compose exec backend python -m app.seeds.rbac_seed
```

Default login after seeding: `superadmin@finyl.app` / `Finyl@2026` — change it
immediately.

**Migrating existing data instead?** Skip seeding — restore your dump per
section 4, which brings the schema and data together.

### 2.4 Verify

```bash
curl -s http://localhost:8000/api/health      # {"status":"ok","service":"finyl-dcp"}
```

Open `http://localhost:8080` and log in.

### 2.5 Domain + TLS (production front door)

Put a TLS-terminating reverse proxy in front of the two published ports. The SPA
must be same-origin with the API, i.e. the domain serves the SPA and proxies
`/api` (and the Daraja `/mpesa/...` callbacks) to the backend.

Caddy (automatic Let's Encrypt) is the least-effort option — example `Caddyfile`:

```
app.example.com {
    encode gzip
    handle /api/*      { reverse_proxy 127.0.0.1:8000 }
    handle /mpesa/*    { reverse_proxy 127.0.0.1:8000 }
    handle /sms/*      { reverse_proxy 127.0.0.1:8000 }
    handle             { reverse_proxy 127.0.0.1:8080 }
}
```

Or nginx + certbot if you prefer. With this topology the SPA calls the API
same-origin, so **no CORS and no `VITE_API_URL` are needed** — keep the defaults.

### 2.6 Database choice

The compose file bundles Postgres with a persistent volume, which is fine for a
single host. For production resilience prefer a **managed Postgres** (RDS, Cloud
SQL, etc.): point `DATABASE_URL` at it, set `DB_SCHEMA=public`, and remove/ignore
the `db` service. Set up backups either way (section 6).

---

## 3. Option B — split hosting (Netlify frontend + backend on a VPS/managed)

Use this when you want the SPA on a CDN/static host separate from the backend.

### 3.1 Backend

Deploy the backend (Option A steps, or just the `backend` image / a plain
`uvicorn app.main:app`) on a VPS or container platform, behind TLS, with a
managed Postgres. Then:

- Set **`ALLOWED_ORIGINS`** to include your Netlify site's origin, e.g.:
  ```
  ALLOWED_ORIGINS=https://finyl-dcp.abacusai.cloud,https://your-site.netlify.app
  ```
  The backend sends `Access-Control-Allow-Origin` only for listed origins and
  keeps `allow_credentials=true` (never a wildcard).
- Note the backend's public URL (e.g. `https://api.example.com`) for the next
  step.

### 3.2 Frontend on Netlify

The repo ships a root **`netlify.toml`** preconfigured with the correct base
directory, build command, publish directory and security headers. In Netlify:

1. Connect the repo. Netlify reads `netlify.toml` — base `frontend`, build
   `npm run build`, publish `frontend/dist`.
2. Set the build environment variable **`VITE_API_URL`** to your backend's
   public URL (no trailing slash), e.g. `https://api.example.com`. This is baked
   in at build time, so redeploy after changing it.
3. Deploy. Netlify builds the SPA and serves the static output.

Because the SPA now calls a cross-origin backend, `ALLOWED_ORIGINS` on the
backend (§3.1) **must** include the Netlify origin, or the browser blocks the
requests.

> Review the `Content-Security-Policy` `connect-src` in `netlify.toml` — it has a
> clearly-marked placeholder for the backend origin that you must edit to your
> real backend URL.

---

## 4. Data migration off Abacus

Migrating carries **both the schema and the data** in one dump, so you do not
replay migrations on the target.

> **CRITICAL — encryption keys travel with the data.** PII columns
> (`national_id` etc.) are encrypted with `FIELD_ENCRYPTION_KEY`; user sessions
> are signed with `JWT_SECRET`. You MUST copy these env values to the new host
> **unchanged**:
> - Wrong/absent `FIELD_ENCRYPTION_KEY` (and `PII_ENCRYPTION_KEY` if it was set)
>   → existing `national_id` ciphertext cannot be decrypted (data effectively
>   lost).
> - Changed `JWT_SECRET` → all existing login sessions are invalidated (users
>   must log in again). Usually acceptable, but know it will happen.

### 4.1 Dump the current (Abacus) database

On the Abacus VM (or anywhere with `DATABASE_URL` for the live DB):

```bash
cd /home/ubuntu/finyl-dcp/backend
set -a; source <(grep '^DATABASE_URL=' .env); set +a
pg_dump -Fc --no-owner --no-privileges "$DATABASE_URL" -f /tmp/finyl_live.dump
```

(Or reuse the latest automated backup dump — see `deploy/DISASTER_RECOVERY.md`.)

### 4.2 Restore into the new database

Provision the target Postgres, then:

```bash
./deploy/restore_db.sh /tmp/finyl_live.dump "<new-target-database-url>"
```

`restore_db.sh` restores the `finyl_dcp` schema. On a dedicated new database you
can then run the app with `DB_SCHEMA=finyl_dcp`, or set `DB_SCHEMA=public` if you
restored into `public`.

### 4.3 Copy the crypto/auth env values

In the new host's `.env`, set — copied verbatim from the old environment:

```
FIELD_ENCRYPTION_KEY=<same value as the source>
PII_ENCRYPTION_KEY=<same value as the source, if it was set>
JWT_SECRET=<same value as the source, to keep sessions valid>
```

### 4.4 Verify decryption

Start the backend against the restored DB, log in, and read back a client that
has a National ID — the `national_id` must come back **decrypted** (plaintext)
via the API. If it comes back empty/garbled, the `FIELD_ENCRYPTION_KEY` does not
match the source — fix the key before going further. A quick DB-level sanity
check that PII decrypts through the app layer:

```bash
docker compose exec backend python -c "
from app.core.database import SessionLocal
from app import models
db = SessionLocal()
b = db.query(models.Borrower).filter(models.Borrower.national_id.isnot(None)).first()
print('decrypted national_id OK' if b and b.national_id else 'NO PII / decryption failed')
db.close()"
```

---

## 5. Cutover (switching production to the new host)

1. **Deploy + migrate** the new host (sections 2–4) and keep it running in
   parallel with Abacus (do not decommission yet).
2. **Point Daraja at the new domain.** Set `DARAJA_CALLBACK_BASE_URL` to the new
   public backend URL and, in the Safaricom Daraja portal, update each tenant's
   registered callback URLs (STK callback, C2B confirmation/validation, B2C
   result/timeout) to the new domain. Callbacks carry the `MPESA_CALLBACK_TOKEN`
   path segment — keep it consistent with the new env.
3. **Tighten the webhook perimeter for production:** `SAFARICOM_IP_ENFORCE=enforce`
   and confirm `SAFARICOM_IP_ALLOWLIST` is current (see
   `deploy/DARAJA_GO_LIVE.md`).
4. **Set the Daraja environment** appropriately: `DARAJA_ENVIRONMENT=production`
   only once you have completed go-live readiness; otherwise keep `sandbox`.
5. **Switch DNS** to the new host and wait for propagation.
6. **Verify** with the checklist below.
7. **Run in parallel** until you are confident, then decommission the Abacus
   deployment.

---

## 6. Backups on the new host

`deploy/backup_db.sh` is host-agnostic. Off Abacus, set `BACKUP_S3_BUCKET`
(and optionally `BACKUP_S3_PREFIX`) plus AWS credentials (standard AWS chain /
instance role) to get off-site dumps; without them the script still produces a
verified **local** dump and only skips the off-site upload (with a warning). See
`deploy/DISASTER_RECOVERY.md` → "Off-Abacus configuration" for the full details
and how to install the systemd timer or a cron job.

---

## 7. Pre-cutover verification checklist

Run through all of these against the NEW host before switching production traffic
and before decommissioning Abacus:

- [ ] **Health:** `curl https://<new-domain>/api/health` → `{"status":"ok",...}`.
- [ ] **TLS valid:** certificate resolves and is trusted (no browser warning).
- [ ] **Login:** an existing user can log in (confirms `JWT_SECRET` + data).
- [ ] **PII decrypts:** create a client with a National ID and read it back — the
      `national_id` returns decrypted (confirms `FIELD_ENCRYPTION_KEY`). For
      migrated data, an existing client's `national_id` also decrypts.
- [ ] **CORS (split deploy only):** the Netlify site can call the backend (no
      browser CORS error); `ALLOWED_ORIGINS` includes the site origin.
- [ ] **Daraja sandbox callback:** a sandbox STK/C2B callback to
      `.../mpesa/<token>/...` returns **200** and creates a row in
      `mpesa_webhook_events` (durable ingestion working).
- [ ] **Callback URLs updated** in the Daraja portal to the new domain, and
      `DARAJA_CALLBACK_BASE_URL` matches.
- [ ] **Backups running:** `deploy/backup_db.sh` completes and produces a dump
      (local, and off-site if `BACKUP_S3_BUCKET` is configured); timer/cron
      installed.
- [ ] **Scheduler:** backend logs show the auto-reconcile worker started (unless
      you intentionally set `SCHEDULER_ENABLED=false`).

Once every box is checked and the new host has run in parallel without issues,
decommission the Abacus deployment.
