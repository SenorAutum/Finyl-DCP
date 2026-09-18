"""
Machine-Readable-Zone (MRZ) parser.

Kenyan travel/identity documents that carry an ICAO 9303 MRZ:

* **Kenyan Passport** — TD3 format: 2 lines of 44 characters.
* **Maisha Card (3rd-gen National ID, 2023+)** — TD1 format: 3 lines of 30 characters.
* **Alien / Foreign-National cards** — TD1 format.

The MRZ is by far the most reliable thing to OCR on a modern ID because it is
printed in the fixed-width OCR-B font and every data group carries a check digit.
We therefore parse it first and trust it over the free-text side of the card.

Public API
----------
``parse_mrz(text)`` scans raw OCR text for an MRZ block, validates the check
digits and returns a normalised field dict (``national_id``/``serial_number``,
``first_name``/``middle_name``/``last_name``, ``date_of_birth``, ``gender``,
``document_type``, ``nationality``, ``expiry_date``) plus a ``_confidence`` map,
or ``None`` when no valid MRZ is present.
"""
from __future__ import annotations

import re
from datetime import date

# Characters legal inside an MRZ (filler is '<').
_MRZ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
_WEIGHTS = (7, 3, 1)


def _clean_line(line: str) -> str:
    """Upper-case, strip spaces, map the handful of glyphs Tesseract confuses in
    the OCR-B font, and keep only legal MRZ characters."""
    s = (line or "").upper().strip()
    s = s.replace(" ", "").replace("«", "<").replace("K<", "K<")
    # Common OCR-B confusions inside the MRZ band.
    s = s.replace("|", "<").replace("^", "<").replace("{", "<").replace("}", "<")
    return "".join(ch for ch in s if ch in _MRZ_CHARS)


def _check_digit(data: str) -> int:
    """ICAO 9303 check-digit over a field. '<' and letters map to 0/10-35."""
    total = 0
    for i, ch in enumerate(data):
        if ch == "<":
            v = 0
        elif ch.isdigit():
            v = int(ch)
        else:
            v = ord(ch) - 55  # 'A'->10 … 'Z'->35
        total += v * _WEIGHTS[i % 3]
    return total % 10


def _matches_check(data: str, check: str) -> bool:
    if not check or not check.isdigit():
        return False
    return _check_digit(data) == int(check)


def _parse_date(yy_mm_dd: str, *, is_dob: bool) -> str | None:
    """Turn a 6-digit YYMMDD MRZ date into an ISO date string."""
    if not re.fullmatch(r"\d{6}", yy_mm_dd or ""):
        return None
    yy, mm, dd = int(yy_mm_dd[:2]), int(yy_mm_dd[2:4]), int(yy_mm_dd[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    today = date.today()
    if is_dob:
        # A DOB is always in the past; pick the century that yields a past date.
        year = 2000 + yy
        if year > today.year:
            year = 1900 + yy
    else:
        # Expiry (or issue) is near the present — bias to 2000s.
        year = 2000 + yy
    try:
        return date(year, mm, dd).isoformat()
    except ValueError:
        return None


def _split_names(surname: str, given: str) -> tuple[str | None, str | None, str | None]:
    """MRZ names use '<' as separators. Surname on its own field; given names
    become first + middle."""
    sur = [p for p in surname.split("<") if p]
    giv = [p for p in given.split("<") if p]
    last = " ".join(w.title() for w in sur) or None
    first = giv[0].title() if giv else None
    middle = " ".join(w.title() for w in giv[1:]) or None
    return first, middle, last


def _gender(code: str) -> str | None:
    c = (code or "").upper()
    if c == "M":
        return "male"
    if c == "F":
        return "female"
    return None


def _find_mrz_lines(text: str) -> list[str]:
    """Return the candidate MRZ lines from raw OCR text: long lines that are
    mostly MRZ characters and contain filler '<'."""
    out = []
    for raw in (text or "").splitlines():
        cleaned = _clean_line(raw)
        if len(cleaned) >= 28 and cleaned.count("<") >= 1:
            # Reject ordinary prose that happens to be long: require a high ratio
            # of A-Z0-9< and at least a couple of digits or filler runs.
            if len(cleaned) / max(len(raw.strip()), 1) >= 0.6:
                out.append(cleaned)
    return out


def _parse_td3(l1: str, l2: str) -> dict | None:
    """Passport: 2×44. Line 2 holds passport-no/DOB/sex/expiry with check digits."""
    l1 = (l1 + "<" * 44)[:44]
    l2 = (l2 + "<" * 44)[:44]
    if not l1.startswith("P"):
        return None
    doc_no = l2[0:9]
    doc_chk = l2[9:10]
    nationality = l2[10:13].replace("<", "")
    dob = l2[13:19]
    dob_chk = l2[19:20]
    sex = l2[20:21]
    expiry = l2[21:27]
    expiry_chk = l2[27:28]

    conf = {}
    # Names from line 1 after the 5-char prefix "P<XXX".
    name_field = l1[5:]
    surname, _, given = name_field.partition("<<")
    first, middle, last = _split_names(surname, given)

    dob_iso = _parse_date(dob, is_dob=True)
    expiry_iso = _parse_date(expiry, is_dob=False)
    doc_clean = doc_no.replace("<", "")

    # Confidence is high when the check digit validates.
    conf["national_id"] = 0.97 if _matches_check(doc_no, doc_chk) else 0.6
    conf["date_of_birth"] = 0.97 if _matches_check(dob, dob_chk) else 0.6
    if first:
        conf["first_name"] = 0.9
    if last:
        conf["last_name"] = 0.9
    if middle:
        conf["middle_name"] = 0.85

    valid_any = _matches_check(doc_no, doc_chk) or _matches_check(dob, dob_chk)
    if not valid_any:
        return None

    out = {
        "document_type": "passport",
        "serial_number": doc_clean or None,
        "national_id": doc_clean or None,
        "first_name": first, "middle_name": middle, "last_name": last,
        "date_of_birth": dob_iso,
        "gender": _gender(sex),
        "nationality": nationality or None,
        "date_of_expiry": expiry_iso,
        "_confidence": conf,
        "_mrz": True,
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def _parse_td1(l1: str, l2: str, l3: str) -> dict | None:
    """ID card (Maisha / Alien): 3×30. Doc-no on line 1, DOB/sex/expiry on line 2,
    names on line 3."""
    l1 = (l1 + "<" * 30)[:30]
    l2 = (l2 + "<" * 30)[:30]
    l3 = (l3 + "<" * 30)[:30]
    doc_type_code = l1[0:2]
    country = l1[2:5].replace("<", "")
    doc_no = l1[5:14]
    doc_chk = l1[14:15]
    optional1 = l1[15:30].replace("<", "")

    dob = l2[0:6]
    dob_chk = l2[6:7]
    sex = l2[7:8]
    expiry = l2[8:14]
    expiry_chk = l2[14:15]
    nationality = l2[15:18].replace("<", "")

    surname, _, given = l3.partition("<<")
    first, middle, last = _split_names(surname, given)
    dob_iso = _parse_date(dob, is_dob=True)
    expiry_iso = _parse_date(expiry, is_dob=False)

    valid_any = _matches_check(doc_no, doc_chk) or _matches_check(dob, dob_chk)
    if not valid_any:
        return None

    # The Kenyan National ID number is often the numeric part of the doc-number or
    # in the optional data. Prefer a clean 7-9 digit run.
    doc_clean = doc_no.replace("<", "")
    nid = None
    for cand in (doc_clean, optional1):
        m = re.search(r"\b(\d{7,9})\b", cand)
        if m:
            nid = m.group(1)
            break

    is_alien = doc_type_code.startswith("A") or (country and country != "KEN")
    conf = {
        "national_id": 0.95 if _matches_check(doc_no, doc_chk) else 0.6,
        "date_of_birth": 0.95 if _matches_check(dob, dob_chk) else 0.6,
    }
    if first:
        conf["first_name"] = 0.9
    if last:
        conf["last_name"] = 0.9
    if middle:
        conf["middle_name"] = 0.85

    out = {
        "document_type": "alien_id" if is_alien else "maisha_card",
        "serial_number": doc_clean or None,
        "national_id": nid or doc_clean or None,
        "first_name": first, "middle_name": middle, "last_name": last,
        "date_of_birth": dob_iso,
        "gender": _gender(sex),
        "nationality": nationality or country or None,
        "date_of_expiry": expiry_iso,
        "_confidence": conf,
        "_mrz": True,
    }
    return {k: v for k, v in out.items() if v not in (None, "")}


def parse_mrz(text: str) -> dict | None:
    """Detect and parse an MRZ block from raw OCR text. Returns a normalised field
    dict (with ``_confidence`` and ``_mrz`` markers) or ``None``."""
    lines = _find_mrz_lines(text)
    if len(lines) < 2:
        return None

    # Try TD3 (passport, 2 lines ~44) over every adjacent pair.
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if a.startswith("P") and 40 <= len(a) <= 46 and 40 <= len(b) <= 46:
            parsed = _parse_td3(a, b)
            if parsed:
                return parsed

    # Try TD1 (ID card, 3 lines ~30) over every adjacent triple.
    for i in range(len(lines) - 2):
        a, b, c = lines[i], lines[i + 1], lines[i + 2]
        if all(26 <= len(x) <= 32 for x in (a, b, c)):
            parsed = _parse_td1(a, b, c)
            if parsed:
                return parsed

    return None
