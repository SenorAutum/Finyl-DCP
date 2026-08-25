# Daraja (M-Pesa) Production Go-Live Runbook

This runbook takes Finyl-DCP's Safaricom Daraja integration from **sandbox** to
**production**. Going live is a **config change, not a code change**: you flip a
single environment variable (`DARAJA_ENVIRONMENT`) and supply real production
credentials, then restart the service.

> **Golden rule:** the default is `DARAJA_ENVIRONMENT=sandbox`. Production is
> only ever reached by a deliberate, manual change. Do **not** flip to
> production until every step in the pre-go-live checklist below is done.

---

## 1. How the environment switch works

A single setting drives everything:

| `DARAJA_ENVIRONMENT` | Daraja base URL used everywhere            |
|----------------------|--------------------------------------------|
| `sandbox` (default)  | `https://sandbox.safaricom.co.ke`          |
| `production`         | `https://api.safaricom.co.ke`              |

The base URL is derived from this single setting in
`backend/app/core/config.py` (`Settings.DARAJA_BASE_URL`) and
`backend/app/services/mpesa.py` (`DarajaCreds.base_url`). It is applied to
**every** Daraja call — OAuth token, B2C payment request, STK push, and C2B URL
registration. There is **no hardcoded sandbox host in any request path**.

Every Daraja credential is read from the environment (`backend/.env` / process
env), never hardcoded:

| Setting                       | Purpose                                            |
|-------------------------------|----------------------------------------------------|
| `DARAJA_ENVIRONMENT`          | `sandbox` \| `production` — the go-live switch     |
| `DARAJA_CONSUMER_KEY`         | OAuth app consumer key                             |
| `DARAJA_CONSUMER_SECRET`      | OAuth app consumer secret                          |
| `DARAJA_SHORTCODE`            | B2C paybill / shortcode (PartyA)                   |
| `DARAJA_INITIATOR_NAME`       | B2C initiator (API operator) username             |
| `DARAJA_SECURITY_CREDENTIAL`  | Initiator password encrypted with Safaricom's cert |
| `DARAJA_PASSKEY`              | Lipa-na-M-Pesa passkey (STK push only)             |
| `DARAJA_CALLBACK_BASE_URL`    | Public host Safaricom posts webhooks back to       |
| `MPESA_CALLBACK_TOKEN`        | Unguessable path segment on every callback URL     |

### Fail-closed safety guard

When `DARAJA_ENVIRONMENT=production`, a B2C payout requires the full set of live
credentials: `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_SHORTCODE`,
`DARAJA_INITIATOR_NAME`, `DARAJA_SECURITY_CREDENTIAL`. If **any** of these is
missing/empty, the app:

1. logs a clear error (`finyl.mpesa` — lists which fields are missing, **no
   secret values**), and
2. **refuses the payout** (`DarajaNotConfigured` → HTTP 422 to the caller).

It will **never** silently simulate a payout or fall back to the sandbox host on
a live request. (In sandbox the behaviour is unchanged: a real sandbox call when
credentials are present, a simulated async acknowledgement when they are not.)

On boot the service logs one secret-free line (logger `finyl.startup`), e.g.:

```
Daraja environment: sandbox (base_url=https://sandbox.safaricom.co.ke, status=...);
consumer key/secret configured: yes; shortcode configured: yes; initiator configured: yes;
security credential configured: yes; passkey configured: yes
```

Use it to confirm which environment the service actually came up in.

---

## 2. Obtain production credentials (Safaricom Daraja portal)

Do this on <https://developer.safaricom.co.ke> with the organisation's Daraja
account (the one that owns the production shortcode).

1. **Create / select a production app** and go through Safaricom's **Go Live**
   process. This yields a **production** Consumer Key and Consumer Secret
   (distinct from the sandbox pair).
2. **Production shortcode / B2C paybill.** Confirm the organisation's live
   B2C shortcode (PartyA for disbursements). For collections you also need the
   paybill/till used for C2B and STK.
3. **Initiator (API operator).** In the M-Pesa Org Portal, create/identify the
   **initiator username** with the **Business Payment** (B2C) role and its
   plaintext initiator password.
4. **Generate the Security Credential.** Encrypt the initiator's plaintext
   password with **Safaricom's PRODUCTION public certificate** (the sandbox
   cert will not work in production):
   - Download the production certificate from the Daraja portal.
   - Encrypt with RSA (PKCS#1 v1.5) and Base64-encode the result. Safaricom's
     portal provides a "Security Credential" generator; or do it locally with
     the production `.cer` file. The resulting Base64 string is
     `DARAJA_SECURITY_CREDENTIAL`.
5. **Passkey** (`DARAJA_PASSKEY`) — required only for STK push (collections);
   obtain the production Lipa-na-M-Pesa passkey for the paybill.

Keep every one of these values out of git — they go only in `backend/.env` on
the server (which is git-ignored) or the platform secret store.

---

## 3. Whitelist the callback URLs on Daraja

Safaricom must be able to POST asynchronous results back to this app. All
callbacks route to the platform host in `DARAJA_CALLBACK_BASE_URL` and carry the
unguessable `MPESA_CALLBACK_TOKEN` as a path segment. The **exact endpoints this
app exposes** (defined in `backend/app/routers/payments.py`, prefix
`/api/v1/payments`) are:

| Purpose                          | Method | Path                                                             |
|----------------------------------|--------|------------------------------------------------------------------|
| B2C **Result** URL               | POST   | `/api/v1/payments/mpesa/{MPESA_CALLBACK_TOKEN}/b2c-result`       |
| B2C **Queue Timeout** URL        | POST   | `/api/v1/payments/mpesa/{MPESA_CALLBACK_TOKEN}/b2c-timeout`      |
| STK push **Callback** URL        | POST   | `/api/v1/payments/mpesa/{MPESA_CALLBACK_TOKEN}/stk-callback`     |
| C2B **Validation/Confirmation**  | POST   | `/api/v1/payments/mpesa/{MPESA_CALLBACK_TOKEN}/c2b-callback`     |

Full public URLs to whitelist (substitute the real host and token):

```
https://<DARAJA_CALLBACK_BASE_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/b2c-result
https://<DARAJA_CALLBACK_BASE_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/b2c-timeout
https://<DARAJA_CALLBACK_BASE_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/stk-callback
https://<DARAJA_CALLBACK_BASE_URL>/api/v1/payments/mpesa/<MPESA_CALLBACK_TOKEN>/c2b-callback
```

Notes:
- The app builds these automatically (`mpesa.callback_url()`), so once
  `DARAJA_CALLBACK_BASE_URL` and `MPESA_CALLBACK_TOKEN` are set, the B2C payload
  and C2B registration already point Safaricom at the right places.
- Register the **B2C Result URL and Queue Timeout URL** against the B2C
  shortcode in the M-Pesa Org Portal / your app config.
- Register the **C2B Validation & Confirmation URLs** via the C2B
  `registerurl` call (the app's `register_c2b_urls()` does this) or the portal.
- The STK **CallBackURL** is sent inline on each STK push — no pre-registration,
  but Safaricom must be able to reach it (public HTTPS).
- Safaricom sends **no auth header** on webhooks; the `MPESA_CALLBACK_TOKEN`
  path segment is the source-auth control. Pair it with the Safaricom IP
  allow-list in `deploy/finyl-dcp.conf` (nginx) for defence-in-depth.

---

## 4. Set the environment variables

On the server, edit `backend/.env` (never commit it). Set the production values
and flip the switch **last**:

```bash
DARAJA_ENVIRONMENT=production
DARAJA_CONSUMER_KEY=<prod consumer key>
DARAJA_CONSUMER_SECRET=<prod consumer secret>
DARAJA_SHORTCODE=<prod B2C shortcode>
DARAJA_INITIATOR_NAME=<prod initiator username>
DARAJA_SECURITY_CREDENTIAL=<Base64 security credential (prod cert)>
DARAJA_PASSKEY=<prod Lipa-na-M-Pesa passkey>        # STK only
DARAJA_CALLBACK_BASE_URL=https://<your production host>
MPESA_CALLBACK_TOKEN=<long random string, unique per environment>
```

Per-DCP (per-tenant) credentials configured in the in-app Configuration screen
continue to override these platform defaults field-by-field, and their
`environment` value participates in the same base-URL selection.

---

## 5. Restart the service

```bash
sudo systemctl restart finyl-dcp
journalctl -u finyl-dcp -n 50 --no-pager
```

Confirm the boot log shows:

```
Daraja environment: production (base_url=https://api.safaricom.co.ke, status=LIVE); ...
```

and that all `configured: yes` booleans are `yes`. If the log still says
`sandbox`, the env var did not load — fix `backend/.env` and restart before
proceeding.

---

## 6. Controlled first live disbursement test

Perform ONE small, controlled real payout to a phone you control (e.g. KES 10)
before enabling normal operations:

1. In the app, create/approve a tiny loan (smallest allowed principal) to a
   borrower phone number **you control**.
2. Trigger the disbursement (B2C). Watch the logs:
   ```bash
   journalctl -u finyl-dcp -f
   ```
3. Expect the loan to move `approved → processing` and an async
   acknowledgement (`ConversationID`) to be recorded.
4. Confirm Safaricom POSTs the **b2c-result** callback and the loan settles
   `processing → active` (or reverts to `approved` on failure). Check the phone
   actually received the funds.
5. Verify the transaction appears under Payments → Transactions with a real
   M-Pesa receipt.

If the payout does **not** settle, check: callback URLs whitelisted & reachable,
`MPESA_CALLBACK_TOKEN` matches the registered URLs, nginx IP allow-list not
blocking Safaricom, and the initiator has the Business Payment role.

---

## 7. Pre-go-live checklist

- [ ] Production Daraja app created; **production** consumer key & secret in hand.
- [ ] Production B2C shortcode confirmed.
- [ ] Initiator username created with **Business Payment (B2C)** role.
- [ ] `DARAJA_SECURITY_CREDENTIAL` generated with the **production** certificate
      (not sandbox).
- [ ] Production STK passkey obtained (if collections/STK is used).
- [ ] `DARAJA_CALLBACK_BASE_URL` points at the real public HTTPS host.
- [ ] `MPESA_CALLBACK_TOKEN` set to a fresh long random value (unique to prod).
- [ ] All four callback URLs whitelisted/registered on Daraja (see §3).
- [ ] nginx Safaricom IP allow-list reviewed (`deploy/finyl-dcp.conf`).
- [ ] `backend/.env` updated; **`.env` is NOT committed to git**.
- [ ] `DARAJA_ENVIRONMENT=production` flipped **last**, after all creds are set.
- [ ] Service restarted; boot log shows `environment: production` + all
      `configured: yes`.
- [ ] Controlled KES-10 live disbursement completed and settled successfully.
- [ ] Rollback understood: set `DARAJA_ENVIRONMENT=sandbox` and restart to
      instantly and safely revert to sandbox.

---

## 8. Rollback

To revert to sandbox at any time (no code change):

```bash
# in backend/.env
DARAJA_ENVIRONMENT=sandbox
```
```bash
sudo systemctl restart finyl-dcp
```

Confirm the boot log reports `environment: sandbox`. Because production fails
closed, an incomplete production config never results in a live payout — the
worst case is a refused disbursement (HTTP 422), never a misdirected one.
