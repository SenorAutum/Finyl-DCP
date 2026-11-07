"""Alternate-phone validation (Phase 2).

Detects the operator (Safaricom / Airtel / Telkom) from the MSISDN prefix. A
name check against the registered account holder needs a name-lookup provider;
until one is configured the result is honestly "format_valid", not "validated".
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
    """Check an alternate phone.

    WP6 (F16): without a name-lookup provider the only thing that can be checked
    is the number's format and operator, so the result is ``format_valid`` -
    never ``validated``. ``validated`` is returned only when a provider returns
    a registered name that matches the client (none is wired yet)."""
    p = normalise(phone)
    operator = detect_operator(p)
    valid_format = bool(re.fullmatch(r"0(7|1)\d{8}", p))
    result = {"phone": p, "operator": operator, "valid_format": valid_format,
              "name_match": None, "registered_name": None, "verified": False}
    if not valid_format:
        result["status"] = "invalid_format"
        return result
    lookup = name_lookup_provider()
    if lookup is None:
        result["status"] = "format_valid"
        result["detail"] = ("Number format and operator checked. The registered name "
                            "was not checked: no name-lookup provider is configured.")
        return result
    reg = lookup(p)
    result["registered_name"] = reg
    if reg and expected_name:
        result["name_match"] = names_match(expected_name, reg)
    if result["name_match"]:
        result["status"] = "validated"
        result["verified"] = True
    else:
        result["status"] = "name_mismatch" if reg else "not_found"
    return result


def name_lookup_provider():
    """Return a callable phone -> registered name, or None when no provider is
    configured. The integration point for a real MNO/aggregator lookup."""
    return None


def names_match(expected: str, registered: str) -> bool:
    """At least two of the client's names appear in the registered name (or
    every name, when the client has fewer than two)."""
    want = [t for t in re.split(r"\s+", (expected or "").lower()) if len(t) > 1]
    got = set(re.split(r"\s+", (registered or "").lower()))
    hits = sum(1 for t in want if t in got)
    return bool(want) and hits >= min(2, len(want))
