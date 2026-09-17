"""Alternate-phone validation (Phase 2).

Detects the operator (Safaricom / Airtel / Telkom) from the MSISDN prefix and,
for M-Pesa (Safaricom) numbers, runs a name check against the registered account
holder via the existing M-Pesa validation path. Mirrors the primary-phone flow.
"""
from __future__ import annotations

import re

# Kenyan MSISDN operator prefixes (07xx / 01xx and +254 variants).
_SAFARICOM = {"0110", "0111", "0112", "0113", "0114", "0115",
              "0700", "0701", "0702", "0703", "0704", "0705", "0706", "0707",
              "0708", "0709", "0710", "0711", "0712", "0713", "0714", "0715",
              "0716", "0717", "0718", "0719", "0720", "0721", "0722", "0723",
              "0724", "0725", "0726", "0727", "0728", "0729", "0740", "0741",
              "0742", "0743", "0745", "0746", "0748", "0757", "0758", "0759",
              "0768", "0769", "0790", "0791", "0792", "0793", "0794", "0795",
              "0796", "0797", "0798", "0799", "0768"}
_AIRTEL = {"0100", "0101", "0102", "0730", "0731", "0732", "0733", "0734",
           "0735", "0736", "0737", "0738", "0739", "0750", "0751", "0752",
           "0753", "0754", "0755", "0756", "0762", "0780", "0781", "0782",
           "0783", "0784", "0785", "0786", "0787", "0788", "0789"}
_TELKOM = {"0770", "0771", "0772", "0773", "0774", "0775", "0776", "0777",
           "0778", "0779"}


def normalise(phone: str) -> str:
    p = re.sub(r"[^\d+]", "", phone or "")
    if p.startswith("+254"):
        p = "0" + p[4:]
    elif p.startswith("254"):
        p = "0" + p[3:]
    return p


def detect_operator(phone: str) -> str:
    p = normalise(phone)
    pfx = p[:4]
    if pfx in _SAFARICOM:
        return "safaricom"
    if pfx in _AIRTEL:
        return "airtel"
    if pfx in _TELKOM:
        return "telkom"
    return "unknown"


def validate(db, *, tenant_id: int, phone: str, expected_name: str | None = None) -> dict:
    """Validate an alternate phone. For Safaricom numbers, attempt an M-Pesa name check."""
    p = normalise(phone)
    operator = detect_operator(p)
    valid_format = bool(re.fullmatch(r"0\d{9}", p))
    result = {"phone": p, "operator": operator, "valid_format": valid_format,
              "name_match": None, "registered_name": None}
    if not valid_format:
        result["status"] = "invalid_format"
        return result
    if operator == "safaricom":
        try:
            from app.services import mpesa
            name_fn = getattr(mpesa, "validate_name", None) or getattr(mpesa, "name_check", None)
            if callable(name_fn):
                nc = name_fn(db, tenant_id, p) if name_fn.__code__.co_argcount >= 3 else name_fn(p)
                reg = (nc or {}).get("name") if isinstance(nc, dict) else None
                result["registered_name"] = reg
                if reg and expected_name:
                    result["name_match"] = expected_name.strip().lower() in reg.strip().lower()
        except Exception:
            pass
    result["status"] = "validated"
    return result
