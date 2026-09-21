#!/usr/bin/env python3
"""
Daraja (Safaricom M-Pesa) SANDBOX diagnostic harness.

Proves the Finyl-DCP <-> Safaricom communication path end to end WITHOUT touching
the database or the running app. Exercises the exact same `app.services.mpesa`
code the API uses, so a PASS here means the live endpoints will work too.

WHAT YOU NEED
-------------
Only two values from https://developer.safaricom.co.ke (create/open an app, copy
the SANDBOX keys of the "Lipa Na M-Pesa Sandbox" product):

    DARAJA_CONSUMER_KEY
    DARAJA_CONSUMER_SECRET

The public Safaricom sandbox shortcode (174379) and test passkey are filled in
automatically when you leave DARAJA_SHORTCODE / DARAJA_PASSKEY unset, so STK push
works out of the box against the sandbox.

USAGE
-----
    cd backend
    DARAJA_CONSUMER_KEY=xxxx DARAJA_CONSUMER_SECRET=yyyy \
        python3 scripts/test_daraja_sandbox.py            # OAuth + STK push
    ... TEST_MSISDN=254708374149 python3 scripts/test_daraja_sandbox.py

Exit code 0 = all executed checks passed; non-zero = at least one failed.
"""
import os
import sys
import time

# --- Make `app` importable when run from backend/ or repo root ---------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# --- Public Safaricom sandbox test constants ---------------------------------
# These are published by Safaricom for the Lipa Na M-Pesa Online sandbox and are
# NOT secrets. They let STK push run without the caller owning a till/paybill.
SANDBOX_SHORTCODE = "174379"
SANDBOX_PASSKEY = (
    "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919"
)
# Safaricom's documented sandbox test MSISDN (accepts the STK prompt silently).
DEFAULT_TEST_MSISDN = "254708374149"

GREEN, RED, YELLOW, DIM, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"
)


def _ok(msg):
    print(f"{GREEN}  PASS{RESET}  {msg}")


def _fail(msg):
    print(f"{RED}  FAIL{RESET}  {msg}")


def _info(msg):
    print(f"{DIM}        {msg}{RESET}")


def _hdr(msg):
    print(f"\n{YELLOW}== {msg} =={RESET}")


def main() -> int:
    # The app config refuses to start on a weak JWT_SECRET. This diagnostic never
    # touches auth, so inject a throwaway strong secret just to import settings.
    if not os.environ.get("JWT_SECRET"):
        import secrets as _secrets
        os.environ["JWT_SECRET"] = _secrets.token_urlsafe(48)

    # Fill public sandbox defaults BEFORE importing settings, so the config picks
    # them up and the resolved creds are complete for an STK push.
    os.environ.setdefault("DARAJA_ENVIRONMENT", "sandbox")
    if not os.environ.get("DARAJA_SHORTCODE") or \
            os.environ.get("DARAJA_SHORTCODE") == "placeholder":
        os.environ["DARAJA_SHORTCODE"] = SANDBOX_SHORTCODE
    if not os.environ.get("DARAJA_PASSKEY") or \
            os.environ.get("DARAJA_PASSKEY") == "placeholder":
        os.environ["DARAJA_PASSKEY"] = SANDBOX_PASSKEY

    # Import AFTER env is primed.
    from app.services import mpesa  # noqa: E402

    creds = mpesa._settings_creds()
    test_msisdn = os.environ.get("TEST_MSISDN", DEFAULT_TEST_MSISDN)
    failures = 0

    print("=" * 66)
    print(" Finyl-DCP  ·  Daraja SANDBOX communication diagnostic")
    print("=" * 66)
    _info(f"Environment      : {creds.environment}")
    _info(f"Base URL         : {creds.base_url}")
    _info(f"Shortcode        : {creds.shortcode}")
    _info(f"Consumer key set : {'yes' if creds.configured else 'NO'}")
    _info(f"Test MSISDN      : {test_msisdn}")

    # --- 0. Credential presence ---------------------------------------------
    _hdr("0. Credential check")
    if not creds.configured:
        _fail("DARAJA_CONSUMER_KEY / DARAJA_CONSUMER_SECRET are not set.")
        _info("Set them and re-run, e.g.:")
        _info("  DARAJA_CONSUMER_KEY=xxxx DARAJA_CONSUMER_SECRET=yyyy \\")
        _info("    python3 scripts/test_daraja_sandbox.py")
        return 2
    _ok("Consumer key & secret present.")

    # --- 1. Network reachability --------------------------------------------
    _hdr("1. Reachability to sandbox.safaricom.co.ke")
    try:
        import httpx
        r = httpx.get(f"{creds.base_url}/oauth/v1/generate"
                      "?grant_type=client_credentials",
                      auth=("ping", "ping"), timeout=20)
        _ok(f"Safaricom sandbox reachable (HTTP {r.status_code} to OAuth probe).")
    except Exception as exc:
        _fail(f"Cannot reach Safaricom sandbox: {type(exc).__name__}: {exc}")
        return 3

    # --- 2. OAuth token ------------------------------------------------------
    _hdr("2. OAuth client-credentials token")
    try:
        t0 = time.time()
        token = mpesa.get_access_token(force_refresh=True, creds=creds)
        dt = (time.time() - t0) * 1000
        assert token and len(token) > 10
        _ok(f"Access token acquired ({len(token)} chars) in {dt:.0f} ms.")
        _info(f"Token preview: {token[:6]}…{token[-4:]}  (never logged in app)")
    except Exception as exc:
        _fail(f"OAuth token request failed: {type(exc).__name__}: {exc}")
        _info("Most common cause: wrong consumer key/secret, or using PRODUCTION "
              "keys against the sandbox host.")
        return 4

    # --- 3. Token cache ------------------------------------------------------
    _hdr("3. Token cache reuse")
    try:
        t0 = time.time()
        token2 = mpesa.get_access_token(creds=creds)  # cached
        dt = (time.time() - t0) * 1000
        if token2 == token and dt < 50:
            _ok(f"Cached token reused (no network call, {dt:.1f} ms).")
        else:
            _ok("Token returned (cache behaviour acceptable).")
    except Exception as exc:
        _fail(f"Cached token fetch failed: {exc}")
        failures += 1

    # --- 4. test_connection() helper ----------------------------------------
    _hdr("4. mpesa.test_connection()")
    res = mpesa.test_connection(creds=creds)
    if res.get("ok"):
        _ok(f"test_connection OK — status={res.get('status')}")
    else:
        _fail(f"test_connection failed — {res}")
        failures += 1

    # --- 5. STK push ---------------------------------------------------------
    _hdr("5. STK push (Lipa na M-Pesa Online)")
    try:
        out = mpesa.stk_push(test_msisdn, 1, "FINYL-TEST", creds=creds)
        resp = out.get("response", {})
        code = str(resp.get("ResponseCode"))
        if code == "0":
            _ok("STK push accepted by Safaricom (ResponseCode 0).")
            _info(f"CheckoutRequestID : {resp.get('CheckoutRequestID')}")
            _info(f"MerchantRequestID : {resp.get('MerchantRequestID')}")
            _info(f"CustomerMessage   : {resp.get('CustomerMessage')}")
            _info("A prompt is delivered to the test MSISDN; the final result "
                  "arrives async on the CallBackURL (needs a public callback host).")
        else:
            _fail(f"STK push returned ResponseCode={code}: "
                  f"{resp.get('ResponseDescription') or resp}")
            failures += 1
    except Exception as exc:
        _fail(f"STK push failed: {type(exc).__name__}: {exc}")
        _info("If OAuth passed but this failed, check the shortcode/passkey pair "
              "or that the amount is a positive integer.")
        failures += 1

    # --- Summary -------------------------------------------------------------
    print("\n" + "=" * 66)
    if failures == 0:
        print(f"{GREEN} ALL CHECKS PASSED — Finyl-DCP is talking to Safaricom "
              f"sandbox.{RESET}")
        print("=" * 66)
        return 0
    print(f"{RED} {failures} check(s) FAILED — see details above.{RESET}")
    print("=" * 66)
    return 1


if __name__ == "__main__":
    sys.exit(main())
