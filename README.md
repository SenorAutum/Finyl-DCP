# Finyl-DCP

**Modular, multi-tenant operations platform for Digital Credit Providers (Kenya).**

Vanilla **React (Vite) + Tailwind CSS** frontend · **FastAPI (Python)** backend · **PostgreSQL** — zero proprietary dependencies, fully portable via Docker.

---

## Modules

| Module key | What it does |
|---|---|
| `lending` | **Client registry with full KYC onboarding** (ID-document capture, Tesseract OCR "Process ID", eKYC identity check, M-Pesa number validation, mobile wallets, next of kin), loan products, full loan lifecycle (`pending → underwriting → approved → active → paid/overdue/defaulted`). Approval and disbursement are **separate, permission-gated steps** (approval no longer auto-disburses): a manager approves via the **Approvals** inbox up to their configurable threshold, then a Disbursement Officer disburses via **live Safaricom Daraja B2C** (credential-gated) (with **maker-checker** above the disbursement threshold). Repeat-cycle applications are blocked with **HTTP 428** until an impact survey is captured. |
| `payments` | **Live Safaricom Daraja** M-Pesa (credential-gated, sandbox/production): B2C disbursement, STK-push collections, C2B repayment webhook (splits principal/interest, updates balances, sends receipt SMS). **Live Uwazii** bulk-SMS hub with per-message provider dispatch log + scheduled jobs (repayment reminders, overdue alerts). |
| `dashboard` | Executive dashboard: PAR 1/30/90, disbursement volume, repayment rate, portfolio yield, monthly trends, status mix, **product × region success heatmap**, **staff net-margin league table** (interest recovered − defaults − cost). Filters: region/branch/product/staff/date. |
| `complaints` | Consumer-protection registry with a **14-day SLA countdown** per ticket (amber ≤ 3 days, red = breached), remedial actions, resolution SMS. |
| `crm` | 5-stage Kanban pipeline (Lead → Contacted → Visit Scheduled → Application → Converted) + **geo-tagged site visits** (GPS capture). |
| `call_center` | Call log + agent scorecard. **Collection Efficiency = promises kept ÷ promises made** (kept = M-Pesa repayment within 3 days of the promise-to-pay date). |
| `impact` | Forced impact surveys on 2nd+ loan cycles, investor dashboard (revenue growth & jobs created **by age group**), and a **P2P mentorship engine** pairing veteran clients with rookies (with match rationale). |
| `cbk_reporting` | AML transaction monitoring (structuring under the KES 1M threshold, rapid small transactions, velocity) + simulated CBK exports: **Asset Quality CSV**, **Capital Adequacy CSV**, **CRB daily pipe-delimited TXT**. |
| `ai_agent` | In-app AI analyst chat. Builds a live pandas snapshot of the tenant's portfolio and sends it as context to any **OpenAI-compatible** endpoint (`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL`). |

## Multi-tenancy & feature flags

- Every row carries a `tenant_id`; every module router is wrapped in `require_module("<key>")` — a disabled flag returns **HTTP 403** and the frontend hides the module from navigation.
- Super Admin → **Module Matrix**: a tenant × module switch grid that toggles flags live.

## Role-based access control (RBAC)

Access is **permission-driven**, not role-hardcoded. A central registry
(`backend/app/core/permissions.py`) defines every fine-grained permission key
(e.g. `loans.approve`, `disburse.execute`, `clients.edit_locked`, `audit.view`)
and maps each **role → set of permissions**. The backend gates endpoints with a
`require_permission("perm.key", ...)` dependency (any-of / all-of); the frontend
loads the signed-in user's permission set at login and gates navigation, routes
and action buttons with a `can(...keys)` helper and `<Can>` wrapper. Adding a
capability to a role is a one-line change to the map — no endpoint edits.

**Roles (per tenant):**

| Role | Can do | Scope |
|---|---|---|
| `super_admin` | Everything, across **all** tenants (tenant switcher, module matrix). | platform |
| `tenant_admin` | Broad superset of all tenant permissions. | tenant |
| `system_admin` | User & access management, branches/regions, role assignment, approval thresholds, payment-file uploads, audit log, backups & data integrity. **Cannot approve loans.** | tenant |
| `relationship_officer` | Create/edit clients (except locked primary fields), initiate loan applications. | **own portfolio only** |
| `branch_manager` | Approve clients & loans up to the branch threshold, reassign loans, edit locked client fields, write-offs. | **branch** |
| `regional_manager` | Approve above the branch limit up to the regional limit. | **region** |
| `disbursement_officer` | Company-wide read + execute M-Pesa B2C disbursement (maker-checker above threshold). | company (read) |
| `reconciliation_officer` | Company-wide read + reconcile payments + issue refunds (maker-checker above threshold). | company (read) |
| `hq_operations` | **Read-only** company-wide dashboards, export & schedule reports, report templates, flag anomalies, view audit. | company (read-only) |

Legacy roles are preserved: `loan_officer` maps to `relationship_officer`
permissions and `call_agent` to a minimal engagement set.

- **Data scoping** is enforced at the query layer: portfolio roles see only their
  own clients/loans, branch roles only their branch, regional roles only their
  region, company roles everything (read).
- **Approval thresholds** live in a configurable table (`approval_thresholds`,
  editable by System Admin) keyed by scope (`role`/`branch`/`region`/`all`).
  Requests over a role's limit are **blocked or auto-escalated**
  branch → region → HQ (`escalation_level` on the loan).
- **Maker-checker:** money movements above the disbursement/refund threshold
  create a `PendingApproval` that a *different* officer must approve
  (initiator ≠ approver is enforced server-side).
- **Audit:** every sensitive action writes an `audit_logs` row; System Admin has
  a filterable **Audit** screen and HQ Operations has read-only access.

## Demo logins (password: `Finyl@2026`)

| Email | Role | Tenant |
|---|---|---|
| superadmin@finyl.app | super_admin | platform (can switch tenants) |
| admin@mularcredit.co.ke | tenant_admin | Mular Credit |
| sysadmin@mularcredit.co.ke | system_admin | Mular Credit |
| ro@mularcredit.co.ke | relationship_officer | Mular Credit |
| branchmgr@mularcredit.co.ke | branch_manager | Mular Credit |
| regionalmgr@mularcredit.co.ke | regional_manager | Mular Credit |
| disburse@mularcredit.co.ke | disbursement_officer | Mular Credit |
| reconcile@mularcredit.co.ke | reconciliation_officer | Mular Credit |
| hqops@mularcredit.co.ke | hq_operations | Mular Credit |
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
| **Process ID (OCR)** | **Hybrid vision-LLM OCR with a local Tesseract fallback.** The vision LLM (`OCR_PROVIDER=vision_llm`, uses the `LLM_*` endpoint) reads the Kenyan National ID front *and* back into structured fields; if it is unavailable or low-confidence, on-device **Tesseract** takes over. Returns the merged fields + a 0–1 confidence per field + which `engine` produced them. Behind an `OcrProvider` interface (`backend/app/services/ocr.py`). Returns a clean **HTTP 503** (not a 500) if no engine is available. |
| **eKYC** | **Live Creditinfo IDM** identity verification in `backend/app/services/ekyc.py`, **credential-gated**: fill `EKYC_BASE_URL/USERNAME/PASSWORD/STRATEGY_ID` to activate. When unconfigured the endpoint returns **HTTP 422** (never a fake pass); `EKYC_MOCK=true` re-enables a deterministic offline mock. Persists `ekyc_status`, `ekyc_reference`, `ekyc_checked_at`. |
| **Validate M-Pesa** | Registered-name / number-format validation (`validate_mobile_number()` in `backend/app/services/mpesa.py`) alongside the live Daraja client. Persists `mpesa_validated`, `mpesa_validation_name`, `mpesa_validated_at` and writes an audit row to `payment_transactions`. |
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

## Integrations module (registry, SMS revenue, message logs)

Every external integration is **live and credential-gated** — the real client is
always wired end-to-end and activates the moment its credentials are supplied. No
mock success is ever returned; an unconfigured integration reports its status
honestly and its endpoints return **HTTP 422**.

**Platform → Integrations** (`/integrations`, super-admin) is the single home for
every integration. It absorbs the former *DCP Setup* screen (`/dcp-setup` now
redirects here) and adds SMS revenue tracking and message auditing. Three tabs:

1. **Registry** — a live status chip for every integration — **LIVE** (configured
   & production), **SANDBOX** (test) or **NOT CONFIGURED** (credential-gated) —
   with masked config, a real **Test connection** button and per-integration
   **test history** (every run is persisted to `integration_test_logs`).
2. **SMS Revenue** — per-DCP SMS usage & revenue with a platform roll-up: messages
   sent, delivery rate, billable count and total sell / cost / margin in KES.
3. **Message Logs** — the paginated, filterable SMS log (send status, delivery
   status, trigger, phone, DCP) with per-message price.

| Integration | Key | Status source | Test action |
|---|---|---|---|
| **Uwazii SMS** | `uwazii_sms` | `UWAZII_ACCESS_TOKEN`/two-step creds → LIVE | two-step auth + real send to `254700000000` (not billed) |
| **M-Pesa (Daraja)** | `daraja_mpesa` | consumer key/secret + `DARAJA_ENV` → LIVE/SANDBOX | Daraja OAuth token |
| **eKYC (Creditinfo IDM)** | `ekyc` | IDM credentials → LIVE (`EKYC_MOCK` → SANDBOX) | runs an identity check |
| **CRB** | `crb` | selected provider's credentials → LIVE | runs a bureau check |
| **ID OCR** | `ocr` | `LLM_API_KEY` → LIVE vision, Tesseract fallback | — (config-derived) |

### SMS revenue & billing

Each successfully **sent** message is **billable** and priced at send time from the
active row of the **`sms_rate_cards`** table (seeded at sell **KES 0.80** / cost
**KES 0.50** → margin **KES 0.30** per SMS). The price is *snapshotted* onto the
`sms_logs` row (`sell_price_kes`, `cost_price_kes`, `margin_kes`, `billable`), so
revenue is unaffected by later rate changes. Non-sent messages are non-billable
with null prices. Rates are read from the table (60 s cache) — never hardcoded; to
change pricing, insert a new active `sms_rate_cards` row (no code change).

Revenue/usage API (RBAC-scoped — super-admin sees all DCPs + roll-up, other roles
their own tenant):

- `GET /api/v1/integrations/sms/usage?from&to&tenant_id&trigger_type`
- `GET /api/v1/integrations/sms/logs?from&to&tenant_id&status&delivery_status&trigger_type&phone&page&page_size`
- `GET /api/v1/integrations/sms/rate` — the current active rate.

### SMS delivery reports (DLR callback)

Uwazii delivery-report callbacks are received at an **unauthenticated** webhook
that matches the message by `provider_ref` and records `delivery_status`
(`delivered` / `failed` / `undelivered`) + `delivered_at`. It accepts JSON or
form-encoded bodies, is defensive (ignores unknown ids, never errors) and lives at:

```
https://finyl-dcp.abacusai.cloud/api/v1/integrations/sms/dlr
```

Configure that URL as the delivery-report / callback URL in the Uwazii account.

### Adding a new integration (extensibility)

Integrations are **first-class and pluggable** — adding one is a *one-place* change
on the backend, and the Registry UI renders it automatically:

1. Implement the client in `backend/app/services/<name>.py` with an
   `integration_status()` returning `"LIVE"` / `"SANDBOX"` / `"NOT CONFIGURED"`.
2. Register it in the `REGISTRY` list in `backend/app/routers/integrations.py`
   with `key`, `name`, `category`, `provider`, its `status` callable, a masked
   `config` callable and an optional `test(db, user)` callable.

No frontend change is needed: `GET /api/v1/integrations` lists it, the Registry tab
renders its card + status chip, and (if `test` is provided) the Test button and
persisted test history work out of the box.

### M-Pesa statement analysis (creditworthiness)

Upload a borrower's **official Safaricom M-Pesa statement PDF** on the Client
profile. It is **parsed entirely on-server** (`pdfplumber` + `pikepdf` for locked
PDFs — no Safaricom API), producing:

- inflow / outflow / net cash-flow, average balance and income regularity;
- a **0–100 affordability score** and a **comfortable installment** estimate;
- **detection of other digital lenders** (Fuliza, M-Shwari, KCB M-Pesa, Tala,
  Branch, Zenka, Timiza, Stawi, Okash, MCo-op, Hustler Fund, …) with amounts
  borrowed/repaid, feeding a monthly external-debt-service figure;
- **integrity checks** that flag possible tampering.

Locked statements default to the client's National ID as the PDF password.

### CRB (Credit Reference Bureau)

A **provider-abstracted** bureau client (`backend/app/services/crb.py`): Metropol
(default), TransUnion and Creditinfo behind one interface. Credential-gated — a
check returns status `not_configured` (never a fabricated score) until the
selected provider's credentials are set. A successful check updates the client's
credit score.

### CBK Reporting compliance posture

CBK Reporting is a **per-tenant feature flag**: a DCP must be CBK-licensed before
it can file reports, so it is **disabled by default for non-compliant tenants**
(the demo's PesaFlow Capital and Jenga Micro) and enabled for the compliant one
(Mular Credit). Toggle it per tenant in **Super Admin → Module Matrix**.

## Architecture

```
frontend/  React 18 + Vite + Tailwind (SPA, mobile-first, collapsible sidebar)
backend/
  app/core/       config, DB engine (schema-aware), JWT security, tenancy deps
  app/models/     tenancy, org, lending, engagement (SQLAlchemy)
  app/routers/    auth, admin, lending, payments, notifications, dashboard,
                  complaints, crm, call_center, impact, cbk, ai, integrations
  app/services/   mpesa (live Daraja + number validation), sms (live Uwazii),
                  ocr (vision-LLM + Tesseract "Process ID"), ekyc (live Creditinfo IDM),
                  crb (credit reference bureau, provider-abstracted),
                  mpesa_statement (M-Pesa statement creditworthiness analysis),
                  storage (client documents), analytics (pandas), aml,
                  mentorship, cbk_exports, ai_agent
  app/seeds/      demo data generator (+ client KYC enrichment)
  migrations/     additive SQL migrations (002_clients_kyc.sql adds the KYC
                  columns/tables; 003_rbac.sql adds approval_thresholds,
                  audit_logs, pending_approvals, report schedules/templates,
                  anomaly flags + loan/borrower scoping columns; 004_live_integrations.sql adds
                  sms provider columns + mpesa_statement_analysis, crb_checks,
                  tenant_integration_config)
deploy/           systemd unit + nginx vhost used for the live VM deployment
```

- **API prefix:** `/api/v1/...`, health check at `/api/health`, interactive docs at `/docs`.
- **Live integrations, credential-gated:** M-Pesa (`mpesa.py`), SMS (`sms.py`, Uwazii), eKYC (`ekyc.py`, Creditinfo IDM) and CRB (`crb.py`) call the real providers. Each ships an `integration_status()` (LIVE / SANDBOX / NOT CONFIGURED) and **activates automatically** once its `DARAJA_*` / `UWAZII_*` / `EKYC_*` / `CRB_*` env vars are supplied — no code change. Unconfigured endpoints return **HTTP 422** rather than faking success. The **Platform → Integrations** module surfaces every status with **Test connection** actions, plus per-DCP SMS revenue and message logs.
- **Schema migrations are additive.** `backend/migrations/002_clients_kyc.sql` is idempotent: it only adds nullable columns to `borrowers` plus the three new KYC tables, so it is safe to run against an existing database.

```bash
psql "$DATABASE_URL" -f backend/migrations/002_clients_kyc.sql
python -m app.seeds.client_kyc          # backfill realistic KYC values on existing clients

psql "$DATABASE_URL" -f backend/migrations/003_rbac.sql   # RBAC tables + scoping columns (idempotent)
python -m app.seeds.rbac_seed           # seed RBAC demo users, thresholds & scope assignments

psql "$DATABASE_URL" -f backend/migrations/004_live_integrations.sql   # SMS provider cols + statement/CRB/integration tables (idempotent)
psql "$DATABASE_URL" -f backend/migrations/005_integrations_module.sql # sms_rate_cards + sms revenue/delivery cols + integration_test_logs (idempotent)
```

## Environment variables

See [.env.example](.env.example):

| Group | Variables |
|---|---|
| Database | `DATABASE_URL`, `DB_SCHEMA` |
| Auth | `JWT_SECRET` |
| AI agent + Vision OCR | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_VISION_MODEL` |
| M-Pesa (Daraja) | `DARAJA_ENV`, `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`, `DARAJA_PASSKEY`, `DARAJA_INITIATOR_NAME`, `DARAJA_SECURITY_CREDENTIAL`, `DARAJA_CALLBACK_BASE_URL` |
| SMS (Uwazii) | `UWAZII_BASE_URL`, `UWAZII_ACCESS_TOKEN`, `UWAZII_SENDER_ID` |
| Client documents | `STORAGE_DIR`, `MAX_UPLOAD_MB` |
| OCR ("Process ID") | `OCR_PROVIDER`, `TESSERACT_CMD`, `OCR_LANGUAGES` |
| eKYC (Creditinfo IDM) | `EKYC_MOCK`, `EKYC_BASE_URL`, `EKYC_USERNAME`, `EKYC_PASSWORD`, `EKYC_STRATEGY_ID` |
| CRB (bureau) | `CRB_PROVIDER`, `CRB_BASE_URL`, `CRB_API_KEY`, `CRB_USERNAME`, `CRB_PASSWORD` |
