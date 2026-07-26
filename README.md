# Finyl-DCP

**Modular, multi-tenant operations platform for Digital Credit Providers (Kenya).**

Vanilla **React (Vite) + Tailwind CSS** frontend · **FastAPI (Python)** backend · **PostgreSQL** — zero proprietary dependencies, fully portable via Docker.

---

## Modules

| Module key | What it does |
|---|---|
| `lending` | **Client registry with full KYC onboarding** (ID-document capture, Tesseract OCR "Process ID", eKYC identity check, M-Pesa number validation, mobile wallets, next of kin), loan products, full loan lifecycle (`pending → underwriting → approved → active → paid/overdue/defaulted`). Approval **auto-disburses via mock Daraja B2C** and fires an SMS. Repeat-cycle applications are blocked with **HTTP 428** until an impact survey is captured. |
| `payments` | Mock Safaricom Daraja M-Pesa: B2C disbursement, STK-push collections, C2B repayment webhook (splits principal/interest, updates balances, sends receipt SMS). SMS hub with dispatch log + scheduled jobs (repayment reminders, overdue alerts). |
| `dashboard` | Executive dashboard: PAR 1/30/90, disbursement volume, repayment rate, portfolio yield, monthly trends, status mix, **product × region success heatmap**, **staff net-margin league table** (interest recovered − defaults − cost). Filters: region/branch/product/staff/date. |
| `complaints` | Consumer-protection registry with a **14-day SLA countdown** per ticket (amber ≤ 3 days, red = breached), remedial actions, resolution SMS. |
| `crm` | 5-stage Kanban pipeline (Lead → Contacted → Visit Scheduled → Application → Converted) + **geo-tagged site visits** (GPS capture). |
| `call_center` | Call log + agent scorecard. **Collection Efficiency = promises kept ÷ promises made** (kept = M-Pesa repayment within 3 days of the promise-to-pay date). |
| `impact` | Forced impact surveys on 2nd+ loan cycles, investor dashboard (revenue growth & jobs created **by age group**), and a **P2P mentorship engine** pairing veteran clients with rookies (with match rationale). |
| `cbk_reporting` | AML transaction monitoring (structuring under the KES 1M threshold, rapid small transactions, velocity) + simulated CBK exports: **Asset Quality CSV**, **Capital Adequacy CSV**, **CRB daily pipe-delimited TXT**. |
| `ai_agent` | In-app AI analyst chat. Builds a live pandas snapshot of the tenant's portfolio and sends it as context to any **OpenAI-compatible** endpoint (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`). |

## Multi-tenancy & feature flags

- Every row carries a `tenant_id`; every module router is wrapped in `require_module("<key>")` — a disabled flag returns **HTTP 403** and the frontend hides the module from navigation.
- **Roles:** `super_admin` (platform-wide, tenant switcher, module matrix), `tenant_admin`, `loan_officer`, `call_agent`.
- Super Admin → **Module Matrix**: a tenant × module switch grid that toggles flags live.

## Demo logins (password: `Finyl@2026`)

| Email | Role | Tenant |
|---|---|---|
| superadmin@finyl.app | super_admin | platform (can switch tenants) |
| admin@mularcredit.co.ke | tenant_admin | Mular Credit |
| officer@mularcredit.co.ke | loan_officer | Mular Credit |
| agent@mularcredit.co.ke | call_agent | Mular Credit |
| admin@pesaflow.co.ke | tenant_admin | PesaFlow Capital |
| admin@jengamicro.co.ke | tenant_admin | Jenga Micro *(CRM + Impact intentionally disabled to demo flag gating)* |

## Quickstart (Docker)

```bash
cp .env.example .env         # set JWT_SECRET (and LLM_* for the AI agent)
docker compose up --build
docker compose exec backend python -m app.seeds.seed --force   # Kenya-flavored demo data
# open http://localhost:8080
```

## Quickstart (bare metal)

```bash
# Backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env      # point DATABASE_URL at your Postgres
python -m app.seeds.seed --force
uvicorn app.main:app --port 8000

# Frontend (dev)
cd ../frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev
```

## Client onboarding & KYC

The `lending` module's **Clients** screen (`/clients`) is a full KYC workstation:

| Feature | How it works |
|---|---|
| **Documents** | Any file type, several at a time, 10 MB each. Files queue in the browser and upload once the client is saved. Stored on the local filesystem under `STORAGE_DIR/clients/<tenant_id>/<client_id>/`, served back through a tenant-scoped download endpoint. |
| **Process ID (OCR)** | Runs **local Tesseract** over every queued JPEG/PNG/PDF (e.g. ID front *and* back), parses Kenyan National ID labels, merges the pages keeping the highest-confidence value per field, and returns the fields + a 0–1 confidence per field + the raw text. Implemented behind an `OcrProvider` interface (`backend/app/services/ocr.py`) so a cloud OCR can be dropped in via `OCR_PROVIDER`. Returns a clean **HTTP 503** (not a 500) if the engine is missing. |
| **eKYC** | Creditinfo IDM-shaped identity verification in `backend/app/services/ekyc.py`. Ships as a deterministic **mock** (`EKYC_MOCK=true`); set it to `false` and fill `EKYC_BASE_URL/USERNAME/PASSWORD/STRATEGY_ID` to call the live provider. Persists `ekyc_status`, `ekyc_reference`, `ekyc_checked_at`. |
| **Validate M-Pesa** | Safaricom registered-name lookup against the National ID (`validate_mobile_number()` in `backend/app/services/mpesa.py`, mock alongside the existing Daraja mocks). Persists `mpesa_validated`, `mpesa_validation_name`, `mpesa_validated_at` and writes an audit row to `payment_transactions`. |
| **Mobile Wallet / Next of Kin** | Tabbed "+ Add row" sub-grids saved in the same request as the client (`client_mobile_wallets`, `client_next_of_kin`). |

**Naming note:** the UI, navigation and API say **Client** (`/api/v1/clients`). The
database table is still `borrowers` and loan/complaint/call rows still reference
`borrower_id` so existing joins, reports and CBK exports keep working.
`/api/v1/borrowers` is mounted as a thin alias of `/api/v1/clients`.

### OCR prerequisites (bare metal)

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng poppler-utils
```

The Docker image installs both automatically.

## Architecture

```
frontend/  React 18 + Vite + Tailwind (SPA, mobile-first, collapsible sidebar)
backend/
  app/core/       config, DB engine (schema-aware), JWT security, tenancy deps
  app/models/     tenancy, org, lending, engagement (SQLAlchemy)
  app/routers/    auth, admin, lending, payments, notifications, dashboard,
                  complaints, crm, call_center, impact, cbk, ai
  app/services/   mpesa (mock Daraja + number validation), sms (mock provider),
                  ocr (Tesseract "Process ID"), ekyc (mock Creditinfo IDM),
                  storage (client documents), analytics (pandas), aml,
                  mentorship, cbk_exports, ai_agent
  app/seeds/      demo data generator (+ client KYC enrichment)
  migrations/     additive SQL migrations (002_clients_kyc.sql adds the KYC columns/tables)
deploy/           systemd unit + nginx vhost used for the live VM deployment
```

- **API prefix:** `/api/v1/...`, health check at `/api/health`, interactive docs at `/docs`.
- **Real integrations:** swap the mock clients in `backend/app/services/mpesa.py`, `sms.py` and `ekyc.py` — the `DARAJA_*`, `SMS_*` and `EKYC_*` env vars are already plumbed through `app/core/config.py`.
- **Schema migrations are additive.** `backend/migrations/002_clients_kyc.sql` is idempotent: it only adds nullable columns to `borrowers` plus the three new KYC tables, so it is safe to run against an existing database.

```bash
psql "$DATABASE_URL" -f backend/migrations/002_clients_kyc.sql
python -m app.seeds.client_kyc          # backfill realistic KYC values on existing clients
```

## Environment variables

See [.env.example](.env.example):

| Group | Variables |
|---|---|
| Database | `DATABASE_URL`, `DB_SCHEMA` |
| Auth | `JWT_SECRET` |
| AI agent | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| M-Pesa (Daraja) | `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`, `DARAJA_PASSKEY` |
| SMS | `SMS_API_URL`, `SMS_API_KEY` |
| Client documents | `STORAGE_DIR`, `MAX_UPLOAD_MB` |
| OCR ("Process ID") | `OCR_PROVIDER`, `TESSERACT_CMD`, `OCR_LANGUAGES` |
| eKYC | `EKYC_MOCK`, `EKYC_BASE_URL`, `EKYC_USERNAME`, `EKYC_PASSWORD`, `EKYC_STRATEGY_ID` |
