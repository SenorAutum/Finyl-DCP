# Finyl-DCP

**Modular, multi-tenant operations platform for Digital Credit Providers (Kenya).**

Vanilla **React (Vite) + Tailwind CSS** frontend · **FastAPI (Python)** backend · **PostgreSQL** — zero proprietary dependencies, fully portable via Docker.

---

## Modules

| Module key | What it does |
|---|---|
| `lending` | Borrower registry (KYC), loan products, full loan lifecycle (`pending → underwriting → approved → active → paid/overdue/defaulted`). Approval **auto-disburses via mock Daraja B2C** and fires an SMS. Repeat-cycle applications are blocked with **HTTP 428** until an impact survey is captured. |
| `payments` | Mock Safaricom Daraja M-Pesa: B2C disbursement, STK-push collections, C2B repayment webhook (splits principal/interest, updates balances, sends receipt SMS). SMS hub with dispatch log + scheduled jobs (repayment reminders, overdue alerts). |
| `dashboard` | Executive dashboard: PAR 1/30/90, disbursement volume, repayment rate, portfolio yield, monthly trends, status mix, **product × region success heatmap**, **staff net-margin league table** (interest recovered − defaults − cost). Filters: region/branch/product/staff/date. |
| `complaints` | Consumer-protection registry with a **14-day SLA countdown** per ticket (amber ≤ 3 days, red = breached), remedial actions, resolution SMS. |
| `crm` | 5-stage Kanban pipeline (Lead → Contacted → Visit Scheduled → Application → Converted) + **geo-tagged site visits** (GPS capture). |
| `call_center` | Call log + agent scorecard. **Collection Efficiency = promises kept ÷ promises made** (kept = M-Pesa repayment within 3 days of the promise-to-pay date). |
| `impact` | Forced impact surveys on 2nd+ loan cycles, investor dashboard (revenue growth & jobs created **by age group**), and a **P2P mentorship engine** pairing veteran borrowers with rookies (with match rationale). |
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

## Architecture

```
frontend/  React 18 + Vite + Tailwind (SPA, mobile-first, collapsible sidebar)
backend/
  app/core/       config, DB engine (schema-aware), JWT security, tenancy deps
  app/models/     tenancy, org, lending, engagement (SQLAlchemy)
  app/routers/    auth, admin, lending, payments, notifications, dashboard,
                  complaints, crm, call_center, impact, cbk, ai
  app/services/   mpesa (mock Daraja), sms (mock provider), analytics (pandas),
                  aml, mentorship, cbk_exports, ai_agent
  app/seeds/      demo data generator
deploy/           systemd unit + nginx vhost used for the live VM deployment
```

- **API prefix:** `/api/v1/...`, health check at `/api/health`, interactive docs at `/docs`.
- **Real integrations:** swap the mock clients in `backend/app/services/mpesa.py` and `sms.py` — the `DARAJA_*` and `SMS_*` env vars are already plumbed through `app/core/config.py`.

## Environment variables

See [.env.example](.env.example): `DATABASE_URL`, `DB_SCHEMA`, `JWT_SECRET`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `DARAJA_CONSUMER_KEY/SECRET/SHORTCODE/PASSKEY`, `SMS_API_URL`, `SMS_API_KEY`.
