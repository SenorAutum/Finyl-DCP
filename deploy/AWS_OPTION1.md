# Finyl-DCP — AWS "Option 1" Deployment Runbook

**Option 1 = AWS App Runner (backend) + Amazon RDS for PostgreSQL (database) + Netlify (frontend).**

This runbook is precise and copy-pasteable, ordered top-to-bottom. It provisions a
production-style deployment of Finyl-DCP on AWS without changing application code —
everything is driven by environment/config. The current live Abacus deployment
(`https://finyl-dcp.abacusai.cloud`) keeps working unchanged; you can run both in
parallel and cut over only after the verification checklist in section (l) passes.

> **Placeholders.** Everything in `<ANGLE_BRACKETS>` is a value you supply. Common
> ones: `<ACCOUNT_ID>` (your 12-digit AWS account id), `<REGION>` (default
> `af-south-1`, Cape Town — see residency note in (a)), `<RDS_ENDPOINT>`,
> `<SUFFIX>` (the random suffix AWS appends to Secrets Manager ARNs).

---

## (m) TL;DR — hand this to an operator or AI agent

Run these in order. Each step is expanded below with full commands, placeholders,
and rationale. **Do not skip (a) prerequisites or (l) verification.**

```
a. Prereqs: AWS account + CLI configured; pick REGION=af-south-1; Netlify account;
   carry over the crypto keys (JWT_SECRET, FIELD_ENCRYPTION_KEY, PII_ENCRYPTION_KEY)
   from the current Abacus backend/.env UNCHANGED if you will migrate existing data.
b. Create RDS Postgres (encrypted, private-in-VPC) + an App Runner VPC connector.
c. Store platform secrets in Secrets Manager; create the App Runner instance IAM
   role (read secrets) + ECR access role.
d. Build the backend image and push to ECR.
e. Create the App Runner service from deploy/aws/apprunner-service-input.json.
f. Bootstrap the DB: create base tables (AUTO_CREATE_TABLES) then run the migration
   runner (backend/scripts/run_migrations.py) against RDS. (Skip base-table + seed
   if you are migrating existing data in step g.)
g. (Optional) Migrate data from Abacus with pg_dump/pg_restore — copy the crypto
   keys UNCHANGED or PII won't decrypt.
h. Deploy the frontend to Netlify; set VITE_API_URL; add the Netlify origin to the
   backend ALLOWED_ORIGINS and update the service.
i. Point M-Pesa/Daraja at the new backend URL (callback base + per-tenant URLs).
j. TLS/custom domain (optional).
k. Backups on AWS (S3 bucket + BACKUP_S3_BUCKET + schedule).
l. Run the PRE-CUTOVER VERIFICATION CHECKLIST. Parallel-run, then decommission.
```

---

## (a) Prerequisites

1. **AWS account** with permissions for RDS, App Runner, ECR, Secrets Manager, IAM,
   EC2 (VPC), and S3.
2. **AWS CLI v2** installed and configured (`aws configure`) with a default region.
   All commands below pass `--region <REGION>` explicitly so you can override.
3. **Docker** installed locally (to build and push the backend image).
4. **Netlify account** (free tier is fine for the pilot) connected to the GitHub repo.
5. **Region — data residency note (owner's decision).** AWS has **no Kenya region**.
   The nearest is **`af-south-1` (Cape Town, South Africa)**, used as the default
   throughout this runbook. Kenya's Data Protection Act (ODPC) permits cross-border
   transfer under conditions; **whether to store Kenyan borrower PII in `af-south-1`
   (or `eu-west-1`, etc.) is a business/legal decision for the data controller — it
   is not a technical blocker.** Set `<REGION>` once and reuse it everywhere.
6. **Carry-over crypto keys (critical if migrating data).** If you will migrate
   existing borrower data from Abacus (step g), you MUST reuse the SAME
   `FIELD_ENCRYPTION_KEY`, `PII_ENCRYPTION_KEY`, and `JWT_SECRET` values from the
   current Abacus `backend/.env`. Different keys ⇒ encrypted PII (national IDs)
   cannot be decrypted and all existing sessions are invalidated. Retrieve them
   from the Abacus deployment before you begin and store them in Secrets Manager in
   step (c). For a **fresh pilot with no data migration**, generate new keys instead.

```bash
# Set these once in your shell for the rest of the runbook:
export REGION=af-south-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "Account=$ACCOUNT_ID Region=$REGION"
```

---

## (b) Create the RDS PostgreSQL instance (+ App Runner VPC connector)

Finyl-DCP runs on PostgreSQL 14+. We provision an **encrypted**, **private**
(`publicly_accessible=false`) instance and let App Runner reach it through a **VPC
connector**.

### b.1 — Generate and store the DB password (never hardcode it)

```bash
# Generate a strong password locally; store it straight into Secrets Manager.
DB_PASSWORD=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
aws secretsmanager create-secret \
  --name finyl-dcp/DB_PASSWORD \
  --description "Finyl-DCP RDS master password" \
  --secret-string "$DB_PASSWORD" \
  --region "$REGION"
```

### b.2 — Create a security group that only the VPC connector can use

```bash
# Use your default VPC (or substitute your own VPC id).
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query "Vpcs[0].VpcId" --output text --region "$REGION")

RDS_SG_ID=$(aws ec2 create-security-group \
  --group-name finyl-dcp-rds-sg \
  --description "Finyl-DCP RDS — Postgres from App Runner VPC connector only" \
  --vpc-id "$VPC_ID" --query GroupId --output text --region "$REGION")

# A dedicated SG for the App Runner VPC connector (its ENIs live here).
APPRUNNER_SG_ID=$(aws ec2 create-security-group \
  --group-name finyl-dcp-apprunner-sg \
  --description "Finyl-DCP App Runner VPC connector" \
  --vpc-id "$VPC_ID" --query GroupId --output text --region "$REGION")

# Allow Postgres (5432) INTO the RDS SG only FROM the App Runner SG.
aws ec2 authorize-security-group-ingress \
  --group-id "$RDS_SG_ID" --protocol tcp --port 5432 \
  --source-group "$APPRUNNER_SG_ID" --region "$REGION"
```

### b.3 — Create the RDS instance (encrypted, private)

```bash
# Subnet group across your VPC's subnets (RDS needs >=2 AZs).
SUBNET_IDS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC_ID \
  --query "Subnets[].SubnetId" --output text --region "$REGION")
aws rds create-db-subnet-group \
  --db-subnet-group-name finyl-dcp-subnets \
  --db-subnet-group-description "Finyl-DCP RDS subnets" \
  --subnet-ids $SUBNET_IDS --region "$REGION"

aws rds create-db-instance \
  --db-instance-identifier finyl-dcp-db \
  --db-name finyl_dcp \
  --engine postgres \
  --engine-version 16 \
  --db-instance-class db.t4g.micro \
  --allocated-storage 20 \
  --storage-type gp3 \
  --storage-encrypted \
  --master-username finyl \
  --master-user-password "$DB_PASSWORD" \
  --no-publicly-accessible \
  --vpc-security-group-ids "$RDS_SG_ID" \
  --db-subnet-group-name finyl-dcp-subnets \
  --backup-retention-period 7 \
  --region "$REGION"

# storage-encrypted uses the default aws/rds KMS key. To use a customer-managed
# key add: --kms-key-id <KMS_KEY_ARN>

# Wait until available, then capture the endpoint:
aws rds wait db-instance-available --db-instance-identifier finyl-dcp-db --region "$REGION"
RDS_ENDPOINT=$(aws rds describe-db-instances --db-instance-identifier finyl-dcp-db \
  --query "DBInstances[0].Endpoint.Address" --output text --region "$REGION")
echo "RDS endpoint: $RDS_ENDPOINT"
```

- `db.t4g.micro` + 20 GB gp3 is a reasonable **pilot** size. *(Cost estimate: roughly
  ~US$12–15/mo for the instance + storage; verify current pricing. Values are estimates.)*
- **PostgreSQL 15 or 16** are both fine (the app targets 14+).

### b.4 — Create the App Runner VPC connector

```bash
# Comma-free list for the CLI:
SUBNET_LIST=$(echo $SUBNET_IDS | tr ' ' ',')
aws apprunner create-vpc-connector \
  --vpc-connector-name finyl-dcp-vpc-connector \
  --subnets ${SUBNET_IDS} \
  --security-groups "$APPRUNNER_SG_ID" \
  --region "$REGION"

# Capture the ARN for the App Runner service input (section e):
VPC_CONNECTOR_ARN=$(aws apprunner list-vpc-connectors --region "$REGION" \
  --query "VpcConnectors[?VpcConnectorName=='finyl-dcp-vpc-connector'].VpcConnectorArn | [0]" \
  --output text)
echo "VPC connector ARN: $VPC_CONNECTOR_ARN"
```

> **Quick-pilot alternative (fewer moving parts, weaker isolation).** Instead of a
> private RDS + VPC connector, you can create RDS with `--publicly-accessible` and
> lock inbound 5432 to a narrow source (e.g. your office IP and/or App Runner's
> egress). Then set `EgressType` to `DEFAULT` in the service input and delete the
> `VpcConnectorArn`. **Tradeoff:** the DB is reachable from the public internet
> (SG-restricted), which is acceptable for a short pilot but not recommended for
> production borrower PII. The private-VPC path above is the default recommendation.

Build the RDS connection URL (used in step c and f). **TLS is enforced via the URL
query string — `?sslmode=require` — with no code change** (psycopg2 honours it):

```
postgresql://finyl:<DB_PASSWORD>@<RDS_ENDPOINT>:5432/finyl_dcp?sslmode=require
```

---

## (c) Store platform secrets in Secrets Manager + create IAM roles

**Only platform-level secrets go here.** Per-tenant M-Pesa/Daraja credentials are
stored **per-tenant, encrypted in the database** (`TenantIntegrationConfig`) — they
are **NOT** placed in Secrets Manager. The Daraja values below are the *platform
default* fallbacks only.

### c.1 — Create the secrets

```bash
# Assemble the DATABASE_URL from the RDS endpoint + the DB password secret.
DB_PASSWORD=$(aws secretsmanager get-secret-value --secret-id finyl-dcp/DB_PASSWORD \
  --query SecretString --output text --region "$REGION")
DATABASE_URL="postgresql://finyl:${DB_PASSWORD}@${RDS_ENDPOINT}:5432/finyl_dcp?sslmode=require"

create_secret () { # name value
  aws secretsmanager create-secret --name "$1" --secret-string "$2" --region "$REGION" >/dev/null \
    && echo "  created $1"
}

# --- Core platform secrets ---
create_secret finyl-dcp/DATABASE_URL "$DATABASE_URL"

# CRYPTO KEYS — if MIGRATING DATA (step g) paste the EXACT values from the Abacus
# backend/.env. For a FRESH pilot, generate new ones as shown:
create_secret finyl-dcp/JWT_SECRET            "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
create_secret finyl-dcp/FIELD_ENCRYPTION_KEY  "$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"
create_secret finyl-dcp/PII_ENCRYPTION_KEY    "$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')"

# M-Pesa callback path token (unguessable segment in webhook URLs; not a crypto secret)
create_secret finyl-dcp/MPESA_CALLBACK_TOKEN  "$(python3 -c 'import secrets;print(secrets.token_hex(12))')"

# --- Daraja PLATFORM defaults (per-tenant creds live encrypted in the DB) ---
create_secret finyl-dcp/DARAJA_CONSUMER_KEY         "<PLATFORM_DARAJA_CONSUMER_KEY>"
create_secret finyl-dcp/DARAJA_CONSUMER_SECRET      "<PLATFORM_DARAJA_CONSUMER_SECRET>"
create_secret finyl-dcp/DARAJA_SHORTCODE            "<PLATFORM_DARAJA_SHORTCODE>"
create_secret finyl-dcp/DARAJA_PASSKEY              "<PLATFORM_DARAJA_PASSKEY>"
create_secret finyl-dcp/DARAJA_SECURITY_CREDENTIAL  "<PLATFORM_DARAJA_SECURITY_CREDENTIAL>"

# --- Other integration secrets (fill real values, or a harmless placeholder) ---
create_secret finyl-dcp/LLM_API_KEY     "<LLM_API_KEY>"
create_secret finyl-dcp/UWAZII_USERNAME "<UWAZII_USERNAME>"
create_secret finyl-dcp/UWAZII_PASSWORD "<UWAZII_PASSWORD>"
create_secret finyl-dcp/UWAZII_SENDER_ID "<UWAZII_SENDER_ID>"
create_secret finyl-dcp/SMS_API_KEY     "<SMS_API_KEY>"
create_secret finyl-dcp/CRB_API_KEY     "<CRB_API_KEY>"
create_secret finyl-dcp/CRB_USERNAME    "<CRB_USERNAME>"
create_secret finyl-dcp/CRB_PASSWORD    "<CRB_PASSWORD>"
create_secret finyl-dcp/EKYC_USERNAME   "<EKYC_USERNAME>"
create_secret finyl-dcp/EKYC_PASSWORD   "<EKYC_PASSWORD>"
create_secret finyl-dcp/EKYC_STRATEGY_ID "<EKYC_STRATEGY_ID>"
```

Get every secret's full ARN (with the `-<SUFFIX>`) for the service input file:

```bash
aws secretsmanager list-secrets --region "$REGION" \
  --query "SecretList[?starts_with(Name,'finyl-dcp/')].[Name,ARN]" --output table
```

### c.2 — App Runner **instance** role (running container reads the secrets)

```bash
cat > /tmp/apprunner-tasks-trust.json <<'JSON'
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Principal": { "Service": "tasks.apprunner.amazonaws.com" },
    "Action": "sts:AssumeRole" } ] }
JSON

aws iam create-role --role-name finyl-dcp-apprunner-instance \
  --assume-role-policy-document file:///tmp/apprunner-tasks-trust.json

# Least-privilege: read only finyl-dcp/* secrets (+ S3 put for backups, section k).
cat > /tmp/apprunner-instance-policy.json <<JSON
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
    "Resource": "arn:aws:secretsmanager:${REGION}:${ACCOUNT_ID}:secret:finyl-dcp/*" }
] }
JSON

aws iam put-role-policy --role-name finyl-dcp-apprunner-instance \
  --policy-name finyl-dcp-secrets-read \
  --policy-document file:///tmp/apprunner-instance-policy.json
```

### c.3 — App Runner **ECR access** role (pulls the image)

```bash
cat > /tmp/apprunner-build-trust.json <<'JSON'
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Principal": { "Service": "build.apprunner.amazonaws.com" },
    "Action": "sts:AssumeRole" } ] }
JSON

aws iam create-role --role-name finyl-dcp-apprunner-ecr-access \
  --assume-role-policy-document file:///tmp/apprunner-build-trust.json
aws iam attach-role-policy --role-name finyl-dcp-apprunner-ecr-access \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

---

## (d) Build the backend image and push to ECR

```bash
aws ecr create-repository --repository-name finyl-dcp-backend --region "$REGION"

ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/finyl-dcp-backend"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Build from the backend/ directory (Dockerfile lives there). App Runner runs
# linux/amd64 — build for that platform explicitly if you are on Apple Silicon.
cd backend
docker build --platform linux/amd64 -t finyl-dcp-backend:latest .
docker tag finyl-dcp-backend:latest "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"
cd ..
```

The backend Dockerfile listens on `$PORT` (default 8000), so App Runner's `Port:
8000` matches. It also bundles `scripts/` and `migrations/` for optional in-image
migration runs.

---

## (e) Create the App Runner service

Edit `deploy/aws/apprunner-service-input.json`, replacing every `<PLACEHOLDER>`
(account id, region, ECR URI, the Secrets Manager ARNs from step c.1, the IAM role
ARNs from c.2/c.3, the VPC connector ARN from b.4, and `ALLOWED_ORIGINS` /
`DARAJA_CALLBACK_BASE_URL`).

> **Strip the `_comment*` keys before calling the API.** The JSON template contains
> `_comment*` annotation keys for clarity; the AWS CLI rejects unknown members.
> Produce a clean copy with `jq`:
>
> ```bash
> jq 'walk(if type=="object" then with_entries(select(.key|startswith("_comment")|not)) else . end)' \
>   deploy/aws/apprunner-service-input.json > /tmp/apprunner-input.clean.json
> # (needs jq >= 1.6 for `walk`; if unavailable, delete the _comment lines by hand)
> ```

```bash
aws apprunner create-service \
  --cli-input-json file:///tmp/apprunner-input.clean.json \
  --region "$REGION"

# Wait until RUNNING and grab the public URL:
SERVICE_ARN=$(aws apprunner list-services --region "$REGION" \
  --query "ServiceSummaryList[?ServiceName=='finyl-dcp-backend'].ServiceArn | [0]" --output text)
aws apprunner describe-service --service-arn "$SERVICE_ARN" --region "$REGION" \
  --query "Service.[Status,ServiceUrl]" --output text
```

- Health check is HTTP `GET /api/health` (returns `{"status":"ok",...}`).
- `AutoDeploymentsEnabled: true` ⇒ every new `:latest` push to ECR (step d) triggers
  an automatic redeploy.
- The `ServiceUrl` (e.g. `https://xxxxxxxx.af-south-1.awsapprunner.com`) is the
  **App Runner base URL** you will use for `VITE_API_URL` and the Daraja callback base.

> **Alternative — source-based build.** Instead of building/pushing to ECR you can
> point App Runner at this GitHub repo with the root `apprunner.yaml` and repository
> source directory `backend`. The ECR-image path above is the **primary / most
> reliable** route; `apprunner.yaml` is the alternative.

---

## (f) Bootstrap the database (fresh pilot)

**Skip this section if you are migrating existing data — do section (g) instead.**

The numbered migrations (`backend/migrations/002…016`) are **additive** changes on
top of the base tables, which are defined by the SQLAlchemy models. So bootstrapping
a brand-new DB is two steps: (1) create the base tables from the models, (2) run the
migration runner for the additive migrations. The simplest place to run both is
**locally, pointed at the RDS endpoint** (you need network reachability to RDS — run
from a host in/peered to the VPC, or temporarily via the quick-pilot public RDS).

```bash
cd backend
# Point at RDS and use the SAME crypto keys you stored in Secrets Manager.
export DATABASE_URL="postgresql://finyl:<DB_PASSWORD>@<RDS_ENDPOINT>:5432/finyl_dcp?sslmode=require"
export DB_SCHEMA=public
export JWT_SECRET="<SAME_JWT_SECRET_AS_SECRETS_MANAGER>"

# 1) Create base tables from the models (one-off, fresh DB only):
AUTO_CREATE_TABLES=true ./venv/bin/python -c "
from app.core.database import Base, engine, ensure_schema
from app import models  # noqa: registers all tables
ensure_schema(); Base.metadata.create_all(bind=engine)
print('base tables created')
"

# 2) Apply the additive migrations (idempotent; tracks applied files):
./venv/bin/python scripts/run_migrations.py
# Expect: "Done: applied 15, skipped 0." on the first run.

# 3) (Optional) seed RBAC roles + a demo tenant/admin for a FRESH pilot ONLY.
#    Do NOT seed if you migrated real data in step (g).
./venv/bin/python -m app.seeds.seed   # review app/seeds/seed.py first
cd ..
```

> **This migration runner replaces the old manual process.** Previously the
> `migrations/*.sql` files were applied by hand via `psql`. `run_migrations.py` now
> applies them in order, idempotently, tracking each in a `schema_migrations` table
> — re-running is safe and only applies new files. A thin wrapper
> `backend/scripts/run_migrations.sh` is also provided.

---

## (g) Migrate data from Abacus (only if carrying existing data)

```bash
# 1) Dump from the CURRENT Abacus database. The repo ships deploy/backup_db.sh which
#    produces a pg_dump -Fc custom-format dump; or run pg_dump directly:
pg_dump "postgresql://<ABACUS_DB_URL>" -Fc -f finyl_dcp_cutover.dump

# 2) Restore into RDS. The Abacus DB uses schema "finyl_dcp"; RDS here uses "public".
#    Remap the schema on restore so objects land in public:
pg_restore --no-owner --no-privileges \
  --schema=finyl_dcp \
  -d "postgresql://finyl:<DB_PASSWORD>@<RDS_ENDPOINT>:5432/finyl_dcp?sslmode=require" \
  finyl_dcp_cutover.dump
# If object names collide with the public schema, restore into a finyl_dcp schema on
# RDS instead (set DB_SCHEMA=finyl_dcp on the service) — keep source and target
# schema names aligned. Do NOT also run section (f): the dump already contains the
# full, migrated schema + data.
```

> **CRITICAL — crypto keys must match.** Copy `FIELD_ENCRYPTION_KEY`,
> `PII_ENCRYPTION_KEY`, and `JWT_SECRET` from the Abacus `backend/.env` into the
> corresponding `finyl-dcp/*` Secrets Manager secrets **unchanged** (step c.1). If
> they differ, encrypted PII (e.g. `borrowers.national_id`) will NOT decrypt and all
> existing user sessions are invalidated. After the service is up (section e/h),
> verify decryption: log in and open a client that has a National ID — it must
> display in cleartext (the field is transparently decrypted on read).

---

## (h) Deploy the frontend to Netlify

1. In Netlify: **Add new site → Import from Git →** pick the GitHub repo. The root
   `netlify.toml` already sets **base `frontend`**, **build `npm run build`**,
   **publish `frontend/dist`**, SPA redirects, and security headers.
2. **Set the API URL.** Site settings → Environment variables →
   **`VITE_API_URL` = the App Runner base URL** from step (e), e.g.:

   ```
   VITE_API_URL = https://xxxxxxxx.af-south-1.awsapprunner.com
   ```

   > **No trailing slash and NO `/api` suffix** — the frontend already prefixes
   > `/api` on every request (`frontend/src/lib/api.js`). A trailing `/api` would
   > produce `/api/api/...` and 404.
3. **Update the CSP `connect-src`** in `netlify.toml` to that same App Runner origin
   (replace the `https://api.example.com` placeholder), commit, and let Netlify
   rebuild. Otherwise the browser blocks API calls.
4. Deploy. Note the Netlify site URL, e.g. `https://<YOUR_SITE>.netlify.app`.
5. **Allow the Netlify origin on the backend.** Set `ALLOWED_ORIGINS` on the App
   Runner service to include the Netlify site (and any custom domain), keeping the
   Abacus origin if you still run it in parallel:

   ```
   ALLOWED_ORIGINS = https://<YOUR_SITE>.netlify.app,https://finyl-dcp.abacusai.cloud
   ```

   Update it in `deploy/aws/apprunner-service-input.json`
   (`RuntimeEnvironmentVariables.ALLOWED_ORIGINS`) and apply:

   ```bash
   aws apprunner update-service --service-arn "$SERVICE_ARN" \
     --source-configuration file:///tmp/apprunner-input.clean.json --region "$REGION"
   ```

   The backend parses `ALLOWED_ORIGINS` into an explicit CORS allowlist (never a
   wildcard with credentials).

---

## (i) Point M-Pesa / Daraja at the new backend

1. Set the platform callback base URL to the App Runner URL (or your custom domain)
   via `DARAJA_CALLBACK_BASE_URL` in the service env, then update the service.
2. **The four Daraja webhook endpoints** (prefix `/api/v1/payments`, with the
   unguessable `MPESA_CALLBACK_TOKEN` path segment) are:

   ```
   POST  https://<BACKEND_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/b2c-result
   POST  https://<BACKEND_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/b2c-timeout
   POST  https://<BACKEND_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/stk-callback
   POST  https://<BACKEND_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/c2b-callback
   ```

3. **Per tenant**, update the registered Daraja callback URLs (STK, C2B validation/
   confirmation, B2C result/timeout) in the Safaricom portal / via the app's
   integration config to use the new `<BACKEND_URL>`. Remember tenant Daraja
   credentials live encrypted per-tenant in the DB — they migrate with the data
   (section g), so only the URLs change.
4. **At go-live**, set `SAFARICOM_IP_ENFORCE=enforce` (rejects non-Safaricom source
   IPs on the callback endpoints) and set `DARAJA_ENVIRONMENT=production` once you
   have production credentials. See `deploy/DARAJA_GO_LIVE.md` for the full checklist.

---

## (j) TLS / custom domain (optional)

- App Runner serves the service over **HTTPS by default** at the `*.awsapprunner.com`
  domain — no action needed for TLS.
- To use a custom API domain (e.g. `api.yourbank.co.ke`): App Runner console →
  service → **Custom domains** → add domain, then create the shown DNS records with
  your registrar. Update `DARAJA_CALLBACK_BASE_URL`, `ALLOWED_ORIGINS`, and the CSP
  accordingly.
- For the frontend: Netlify → Domain settings → add your custom domain (Netlify
  provisions TLS automatically). Add that domain to backend `ALLOWED_ORIGINS` and the
  CSP `connect-src` too.

---

## (k) Backups on AWS

The repo's `deploy/backup_db.sh` is host-agnostic: outside Abacus it uploads to S3
when `BACKUP_S3_BUCKET` is set (credentials from the default AWS chain / instance
role). `deploy/restore_db.sh` restores.

```bash
# 1) Create a private, encrypted backups bucket.
aws s3api create-bucket --bucket finyl-dcp-backups-<ACCOUNT_ID> \
  --region "$REGION" --create-bucket-configuration LocationConstraint="$REGION"
aws s3api put-bucket-encryption --bucket finyl-dcp-backups-<ACCOUNT_ID> \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket finyl-dcp-backups-<ACCOUNT_ID> \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 2) Grant whatever runs the backup s3:PutObject on that bucket (add to the App
#    Runner instance role, or a dedicated backup task role).
```

Then set `BACKUP_S3_BUCKET=finyl-dcp-backups-<ACCOUNT_ID>` (and optional
`BACKUP_S3_PREFIX`) in the backup runner's environment.

**Scheduling — pragmatic choice.** App Runner is request-driven and not a good place
for a cron/systemd timer. Simplest reliable options:
- **EventBridge Scheduler → a small Fargate task** that runs `deploy/backup_db.sh`
  on a schedule (e.g. daily 02:00). Most "serverless" and cheapest to leave running.
- **Or** a tiny always-on `t4g.nano` EC2 instance running the existing systemd timer
  from `deploy/RUNNING_INDEPENDENTLY.md`.

Pick one; the Fargate + EventBridge route is recommended to avoid an always-on box.
*(Costs are minor and are estimates — verify current pricing.)*

---

## (l) PRE-CUTOVER VERIFICATION CHECKLIST

Run **all** of these against the AWS deployment before sending real traffic. Keep the
Abacus deployment running in parallel until every item passes.

- [ ] **Health.** `curl -s https://<BACKEND_URL>/api/health` → `{"status":"ok",...}` (HTTP 200).
- [ ] **Login.** Authenticate via the Netlify frontend (or `POST /api/v1/auth/login`) and receive a token.
- [ ] **Client + PII decrypt.** Create a client with a National ID, then read it back
      via the API/UI — the `national_id` displays in cleartext (proves
      FIELD_ENCRYPTION_KEY/PII_ENCRYPTION_KEY are correct).
- [ ] **Daraja callback (sandbox).** Post a sandbox callback to
      `/api/v1/payments/mpesa/<TOKEN>/stk-callback` → HTTP 200 and a row is written
      to the webhook events table (`mpesa_webhook_events`).
- [ ] **Migrations tracked.** `SELECT filename FROM schema_migrations ORDER BY filename;`
      lists all applied migration files (15 for a fresh bootstrap).
- [ ] **Backups.** Run `deploy/backup_db.sh` → a dump is produced locally AND
      uploaded to the S3 bucket.
- [ ] **TLS.** The App Runner URL and Netlify URL both serve valid HTTPS.
- [ ] **CORS.** The frontend calls the API with no browser CORS error
      (`ALLOWED_ORIGINS` includes the Netlify origin; CSP `connect-src` includes the
      backend origin).

Once all pass: switch DNS / users to the Netlify + App Runner stack, monitor, then
decommission the Abacus deployment.

---

## Cost awareness (rough estimates — verify current pricing)

- **App Runner**: billed for provisioned + active instances; the 0.25 vCPU/0.5 GB
  minimum keeps the pilot cheap. There is a small always-provisioned baseline cost.
- **RDS `db.t4g.micro` + 20 GB gp3**: on the order of ~US$12–15/month.
- **ECR**: image storage is a few cents/GB-month.
- **Secrets Manager**: ~US$0.40 per secret/month.
- **S3 backups**: pennies at pilot volumes.

All figures are estimates for planning only — confirm against the AWS pricing pages
for `<REGION>`.

---

### Related docs
- `deploy/RUNNING_INDEPENDENTLY.md` — host-agnostic run/deploy (Docker, split deploy, backups).
- `deploy/DISASTER_RECOVERY.md` — backup/restore and recovery procedures.
- `deploy/DARAJA_GO_LIVE.md` — Safaricom Daraja production go-live checklist.
- `deploy/aws/apprunner-service-input.json` — the App Runner service definition edited in section (e).
- `apprunner.yaml` — optional source-based build alternative.
