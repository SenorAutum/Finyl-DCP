"""
OCR service — the engine behind the "Process ID" action on the client screen.

Design
------
`OcrProvider` is a tiny interface with a single method (`image_to_text`). The
default implementation runs **Tesseract locally** (no cloud dependency, no
per-page cost, fully portable). To use a cloud OCR / eKYC vendor instead:

    class VendorOcrProvider(OcrProvider):
        def image_to_text(self, data: bytes, mime_type: str) -> str:
            ...POST the bytes to the vendor, return the text...

    # then register it:
    OCR_PROVIDERS["vendor"] = VendorOcrProvider

and set OCR_PROVIDER=vendor in the environment. Field parsing (below) is engine
agnostic, so nothing else changes.

Parsing
-------
`parse_kenyan_id(text)` extracts the fields printed on a Kenyan National ID.
Front of the card carries serial number, ID number, full names, date of birth,
sex, district of birth, place of issue and date of issue; the back carries
district / division / location / sub-location. `merge_results()` therefore folds
several files (e.g. front + back scans) into ONE field set, keeping the highest
confidence value per field.
"""
from __future__ import annotations

import io
import os
import re
import shutil
from datetime import date, datetime

from app.core.config import settings
from app.services import mrz as mrz_parser


class OcrUnavailable(RuntimeError):
    """Raised when the configured OCR engine is not installed/reachable."""


# --------------------------------------------------------------------------- #
# Provider interface
# --------------------------------------------------------------------------- #
class OcrProvider:
    """Minimal OCR contract: bytes in, plain text out."""

    name = "base"

    def available(self) -> tuple[bool, str]:
        """(is_usable, human readable reason when not)."""
        raise NotImplementedError

    def image_to_text(self, data: bytes, mime_type: str, filename: str = "") -> str:
        raise NotImplementedError


class TesseractOcrProvider(OcrProvider):
    """Local Tesseract engine. PDFs are rasterised page-by-page with poppler."""

    name = "tesseract"

    def available(self) -> tuple[bool, str]:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except Exception as exc:                      # pragma: no cover
            return False, f"Python OCR libraries missing ({exc}). Install pytesseract + pillow."
        cmd = settings.TESSERACT_CMD
        if not (os.path.isabs(cmd) and os.path.exists(cmd)) and not shutil.which(cmd):
            return False, ("Tesseract binary not found. Install it with "
                           "`apt-get install -y tesseract-ocr` (and poppler-utils for PDFs).")
        return True, ""

    @staticmethod
    def _preprocess(img):
        """Clean up a phone photo for OCR: orient, upscale, grayscale, boost
        contrast and sharpen. Massively improves Tesseract accuracy on the small,
        low-contrast text of a laminated ID card."""
        from PIL import Image, ImageOps, ImageFilter
        try:
            img = ImageOps.exif_transpose(img)          # honour camera rotation
        except Exception:
            pass
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        # Upscale small photos — Tesseract wants ~300 DPI equivalent.
        if max(img.size) < 1800:
            factor = 1800 / max(img.size)
            img = img.resize((int(img.width * factor), int(img.height * factor)),
                             Image.LANCZOS)
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray, cutoff=2)     # stretch dynamic range
        gray = gray.filter(ImageFilter.SHARPEN)
        return gray

    def image_to_text(self, data: bytes, mime_type: str, filename: str = "") -> str:
        ok, why = self.available()
        if not ok:
            raise OcrUnavailable(why)
        import pytesseract
        from PIL import Image

        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
        is_pdf = (mime_type or "").endswith("pdf") or filename.lower().endswith(".pdf")
        # PSM 4 = assume a single column of text of variable sizes — best fit for
        # an ID card. Also OCR the MRZ band, which uses the OCR-B font.
        cfg = "--oem 3 --psm 4"

        if is_pdf:
            try:
                from pdf2image import convert_from_bytes
            except Exception as exc:
                raise OcrUnavailable(f"PDF support needs pdf2image + poppler-utils ({exc})")
            try:
                pages = convert_from_bytes(data, dpi=300, first_page=1, last_page=4)
            except Exception as exc:
                raise OcrUnavailable(f"Could not rasterise the PDF — is poppler-utils installed? ({exc})")
            return "\n".join(
                pytesseract.image_to_string(self._preprocess(p),
                                            lang=settings.OCR_LANGUAGES, config=cfg)
                for p in pages)

        img = Image.open(io.BytesIO(data))
        proc = self._preprocess(img)
        return pytesseract.image_to_string(proc, lang=settings.OCR_LANGUAGES, config=cfg)


# --------------------------------------------------------------------------- #
# Vision-LLM provider (primary) — reads the ID directly with a multimodal model
# --------------------------------------------------------------------------- #
# Fields the model must return. Kept in sync with parse_kenyan_id() output so the
# frontend and Borrower model see an identical shape regardless of engine.
VISION_FIELDS = [
    "document_type", "serial_number", "national_id", "first_name", "middle_name",
    "last_name", "full_name", "date_of_birth", "sex", "nationality",
    "district_of_birth", "place_of_issue", "date_of_issue", "date_of_expiry",
    "district", "division", "location", "sub_location",
]

_VISION_PROMPT = (
    "You are a precise OCR engine for East African identity documents. You are "
    "given one or more images (front and/or back, or multiple pages of the SAME "
    "document). Read every printed field and return ONLY a single minified JSON "
    "object — no markdown, no prose. Use these exact keys: "
    + ", ".join(VISION_FIELDS) + ". "
    "The document may be any of these types — set 'document_type' accordingly: "
    "'national_id' (2nd-generation Kenyan ID, laminated), 'maisha_card' "
    "(3rd-generation Kenyan polycarbonate ID / Maisha Namba, 2023+), 'passport' "
    "(Kenyan or other passport), 'alien_id' (foreign national / alien card), or "
    "'other'. "
    "Rules: dates MUST be ISO format YYYY-MM-DD. 'sex' is 'male' or 'female'. "
    "'national_id' is the ID/serial NUMBER (7-9 digits for a Kenyan ID; the "
    "document number for a passport/alien card). 'serial_number' is the longer "
    "document serial when distinct. If the document has a Machine-Readable Zone "
    "(the two or three rows of monospaced text with '<' characters at the bottom), "
    "read it carefully — it is the most reliable source for names, number, date "
    "of birth and expiry. Merge front and back (and the MRZ) into one object. If "
    "a field is not visible use null. Do not invent values."
)


class VisionLlmOcrProvider(OcrProvider):
    """Multimodal-LLM National-ID reader via any OpenAI-compatible vision endpoint
    (LLM_BASE_URL / LLM_API_KEY / LLM_VISION_MODEL|LLM_MODEL). Returns structured
    fields directly (no regex parsing needed)."""

    name = "vision_llm"

    def available(self) -> tuple[bool, str]:
        import re as _re
        key = (settings.LLM_API_KEY or "").strip()
        base = (settings.LLM_BASE_URL or "").strip()
        if not key or key == "sk-placeholder" or "placeholder" in key.lower():
            return False, "LLM_API_KEY not configured for vision OCR."
        if not base or "api.openai.com" in base and key == "sk-placeholder":
            return False, "LLM_BASE_URL not configured for vision OCR."
        return True, ""

    def image_to_text(self, data: bytes, mime_type: str, filename: str = "") -> str:
        # Not used directly — vision path returns structured fields via process().
        raise NotImplementedError

    def _to_image_parts(self, files: list[tuple[str, str, bytes]]) -> list[dict]:
        """Convert each upload to a data-URL image part. PDFs are rasterised."""
        import base64
        parts: list[dict] = []
        for filename, mime, data in files:
            is_pdf = (mime or "").endswith("pdf") or filename.lower().endswith(".pdf")
            if is_pdf:
                try:
                    from pdf2image import convert_from_bytes
                    pages = convert_from_bytes(data, dpi=200, first_page=1, last_page=2)
                except Exception as exc:
                    raise OcrUnavailable(f"PDF rasterise failed (poppler-utils?) ({exc})")
                for pg in pages:
                    buf = io.BytesIO()
                    pg.convert("RGB").save(buf, format="JPEG", quality=85)
                    b64 = base64.b64encode(buf.getvalue()).decode()
                    parts.append({"type": "image_url",
                                  "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
            else:
                use_mime = mime if (mime or "").startswith("image/") else "image/jpeg"
                b64 = base64.b64encode(data).decode()
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:{use_mime};base64,{b64}"}})
        return parts

    def process(self, files: list[tuple[str, str, bytes]]) -> dict:
        """Return {fields, confidence, raw_text} extracted by the vision model."""
        import httpx

        # A dedicated vision model is required: the general LLM_MODEL (e.g. a
        # text-only routing alias) may not accept image input. Default to a
        # known vision-capable model when LLM_VISION_MODEL is unset.
        model = (settings.LLM_VISION_MODEL or "gpt-4o").strip()
        content = [{"type": "text", "text": _VISION_PROMPT}] + self._to_image_parts(files)
        resp = httpx.post(
            f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": model,
                  "messages": [{"role": "user", "content": content}],
                  "max_tokens": 900, "temperature": 0},
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]
        fields = _extract_json(raw)
        if not fields:
            raise OcrUnavailable("Vision model returned no parseable JSON.")

        # Normalise → keep only known keys with truthy values; map sex→gender.
        out: dict = {}
        for k in VISION_FIELDS:
            v = fields.get(k)
            if v in (None, "", "null"):
                continue
            if k == "sex":
                sv = str(v).strip().lower()
                out["gender"] = "female" if sv.startswith("f") else ("male" if sv.startswith("m") else None)
                if out["gender"] is None:
                    out.pop("gender", None)
            else:
                out[k] = str(v).strip() if not isinstance(v, str) else v.strip()
        # Vision extraction is high-confidence for populated fields.
        confidence = {k: 0.95 for k in out}
        return {"fields": out, "confidence": confidence,
                "raw_text": raw[:20000]}


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM response (handles code fences)."""
    if not text:
        return None
    import json as _json
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?", "", t).rsplit("```", 1)[0].strip()
    try:
        return _json.loads(t)
    except Exception:
        pass
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            return _json.loads(m.group(0))
        except Exception:
            return None
    return None


OCR_PROVIDERS: dict[str, type[OcrProvider]] = {
    "tesseract": TesseractOcrProvider,
    "vision_llm": VisionLlmOcrProvider,
}


def get_provider() -> OcrProvider:
    cls = OCR_PROVIDERS.get(settings.OCR_PROVIDER, TesseractOcrProvider)
    return cls()


# --------------------------------------------------------------------------- #
# Kenyan National ID parsing
# --------------------------------------------------------------------------- #
# OCR frequently mangles label spelling/spacing, so every label is matched with a
# tolerant pattern and the value is taken up to the end of the line.
_LABELS: dict[str, str] = {
    "serial_number": r"seri[a4]?l\s*(?:no|number|nurnber)?",
    # "SERIAL NUMBER" must not be read as the ID number — require an ID prefix.
    "national_id": r"(?:id|identity\s*(?:card)?)\s*(?:no\.?|number|nurnber)",
    "full_names": r"full\s*n[a4]m[e3]?s?",
    "date_of_birth": r"d[a4]t[e3]\s*of\s*birth",
    "sex": r"se[xk]",
    "district_of_birth": r"distr[il1]ct\s*of\s*birth",
    "place_of_issue": r"pl[a4]c[e3]\s*of\s*issu[e3]",
    "date_of_issue": r"d[a4]t[e3]\s*of\s*issu[e3]",
    # Plain DISTRICT — never the "DISTRICT OF BIRTH" line.
    "district": r"distr[il1]ct(?!\s*of\s*birth)",
    "division": r"div[il1]s[il1]on",
    # Plain LOCATION — never the "SUB-LOCATION" line.
    "location": r"(?<!sub)(?<!sub-)(?<!sub )(?<!sub_)loc[a4]t[il1]on",
    "sub_location": r"sub[\s\-_]*loc[a4]t[il1]on",
}

_DATE_RE = re.compile(r"(\d{1,2})[.\-/\s](\d{1,2})[.\-/\s](\d{2,4})")
_ID_RE = re.compile(r"\b(\d{7,9})\b")
_SERIAL_RE = re.compile(r"\b(\d{8,12})\b")


def _clean(value: str) -> str:
    value = re.sub(r"^[\s:;.\-_=|]+", "", value or "")
    value = re.sub(r"[\s:;.\-_=|]+$", "", value)
    return re.sub(r"\s{2,}", " ", value).strip()


def _find(text: str, label_key: str) -> str | None:
    """Value that follows a label, either on the same line or the next one."""
    pattern = _LABELS[label_key]
    for m in re.finditer(pattern, text, re.I):
        tail = text[m.end():]
        line, _, rest = tail.partition("\n")
        value = _clean(line)
        if not value:                       # label on its own line → value below
            value = _clean(rest.split("\n")[0] if rest else "")
        if value:
            return value
    return None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = _DATE_RE.search(raw)
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    if y < 100:
        y += 2000 if y < 40 else 1900
    if d > 31 or mth > 12 or not (1900 <= y <= date.today().year + 1):
        return None
    try:
        return date(y, mth, d)
    except ValueError:
        return None


def _split_names(raw: str | None) -> tuple[str | None, str | None, str | None]:
    if not raw:
        return None, None, None
    parts = [p.title() for p in re.split(r"\s+", re.sub(r"[^A-Za-z\s'\-]", " ", raw)) if len(p) > 1]
    if not parts:
        return None, None, None
    if len(parts) == 1:
        return parts[0], None, None
    if len(parts) == 2:
        return parts[0], None, parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def parse_kenyan_id(text: str) -> dict:
    """Extract ID fields from raw OCR text. Values are None when not found;
    `_confidence` carries a 0-1 score per populated field."""
    out: dict = {}
    conf: dict = {}

    def put(key, value, score):
        if value not in (None, ""):
            out[key] = value
            conf[key] = round(score, 2)

    serial_raw = _find(text, "serial_number")
    if serial_raw:
        m = _SERIAL_RE.search(serial_raw.replace(" ", ""))
        put("serial_number", m.group(1) if m else serial_raw[:30], 0.9 if m else 0.5)

    id_raw = _find(text, "national_id")
    if id_raw:
        m = _ID_RE.search(id_raw.replace(" ", ""))
        put("national_id", m.group(1) if m else None, 0.92)

    first, middle, last = _split_names(_find(text, "full_names"))
    put("first_name", first, 0.85)
    put("middle_name", middle, 0.8)
    put("last_name", last, 0.85)

    dob = _parse_date(_find(text, "date_of_birth"))
    put("date_of_birth", dob.isoformat() if dob else None, 0.88)
    doi = _parse_date(_find(text, "date_of_issue"))
    put("date_of_issue", doi.isoformat() if doi else None, 0.85)

    sex_raw = (_find(text, "sex") or "").upper()
    if sex_raw.startswith("M") or "MALE" in sex_raw and "FEMALE" not in sex_raw:
        put("gender", "male", 0.9)
    elif sex_raw.startswith("F") or "FEMALE" in sex_raw:
        put("gender", "female", 0.9)

    def place(key, label, score=0.8):
        raw = _find(text, label)
        if raw:
            value = _clean(re.sub(r"[^A-Za-z\s'\-/]", " ", raw)).title()
            if 1 < len(value) <= 60:
                put(key, value, score)

    place("district_of_birth", "district_of_birth", 0.82)
    place("place_of_issue", "place_of_issue", 0.82)
    place("sub_location", "sub_location", 0.78)
    place("division", "division", 0.78)
    place("location", "location", 0.75)
    place("district", "district", 0.75)

    # Tag the document type from the free-text side. A card with a district-of-
    # birth / place-of-issue block is the classic 2nd-gen National ID; anything
    # else is refined by the MRZ parser (passport / maisha_card / alien_id).
    if any(out.get(k) for k in ("national_id", "serial_number", "first_name")):
        low = text.lower()
        if "passport" in low:
            put("document_type", "passport", 0.6)
        elif "maisha" in low or "kadi ya maisha" in low:
            put("document_type", "maisha_card", 0.6)
        elif "alien" in low or "refugee" in low or "foreign national" in low:
            put("document_type", "alien_id", 0.6)
        else:
            put("document_type", "national_id", 0.55)

    out["_confidence"] = conf
    return out


def merge_results(results: list[dict]) -> dict:
    """Fold per-file field sets into one, keeping the highest-confidence value."""
    merged: dict = {}
    best: dict = {}
    for r in results:
        conf = r.get("_confidence", {})
        for key, value in r.items():
            if key in ("_confidence", "file"):
                continue
            score = conf.get(key, 0.5)
            if key not in merged or score > best.get(key, 0):
                merged[key] = value
                best[key] = score
    merged["_confidence"] = best
    return merged


def _tesseract_extract(files: list[tuple[str, str, bytes]]) -> dict:
    """Run the local Tesseract engine over every file and regex-parse the text."""
    provider = TesseractOcrProvider()
    ok, why = provider.available()
    if not ok:
        raise OcrUnavailable(why)
    per_file, raw_chunks = [], []
    for filename, mime, data in files:
        text = provider.image_to_text(data, mime, filename)
        raw_chunks.append(f"----- {filename} -----\n{text.strip()}")
        # 1) Free-text regex parse of the human-readable side of the card.
        per_file.append({"file": filename, **parse_kenyan_id(text)})
        # 2) MRZ parse (passport / Maisha card / alien card). The MRZ is check-
        #    digit validated, so when present it is the most reliable source and
        #    is merged with higher confidence than the free-text fields.
        mrz_fields = mrz_parser.parse_mrz(text)
        if mrz_fields:
            conf = mrz_fields.pop("_confidence", {})
            mrz_fields.pop("_mrz", None)
            per_file.append({"file": filename, "_confidence": conf, **mrz_fields})
    merged = merge_results(per_file)
    confidence = merged.pop("_confidence", {})
    merged.pop("file", None)
    return {"engine": provider.name, "fields": merged, "confidence": confidence,
            "raw_text": "\n\n".join(raw_chunks)}


def process_id_files(files: list[tuple[str, str, bytes]]) -> dict:
    """files = [(filename, mime_type, data)]. Returns merged National-ID fields +
    per-field confidence + raw text + which engine produced them.

    Hybrid strategy: when OCR_PROVIDER=vision_llm and the LLM is configured, the
    multimodal model reads the ID directly (best accuracy on phone photos). If the
    vision call is unconfigured or fails for any reason, it falls back to local
    Tesseract. Raises OcrUnavailable only when NO engine can run, so the router
    answers with a clear 503 instead of a 500.
    """
    engine_notes: list[str] = []

    if settings.OCR_PROVIDER == "vision_llm":
        vision = VisionLlmOcrProvider()
        ok, why = vision.available()
        if ok:
            try:
                result = vision.process(files)
                return {
                    "engine": "vision_llm",
                    "engine_notes": engine_notes,
                    "files_processed": len(files),
                    "fields": result["fields"],
                    "confidence": result["confidence"],
                    "raw_text": result["raw_text"],
                    "extracted_at": datetime.utcnow().isoformat() + "Z",
                }
            except Exception as exc:
                engine_notes.append(f"Vision OCR failed, fell back to Tesseract ({exc}).")
        else:
            engine_notes.append(f"Vision OCR unavailable ({why}); using Tesseract.")

    # Tesseract path (default fallback)
    result = _tesseract_extract(files)
    return {
        "engine": result["engine"],
        "engine_notes": engine_notes,
        "files_processed": len(files),
        "fields": result["fields"],
        "confidence": result["confidence"],
        "raw_text": result["raw_text"],
        "extracted_at": datetime.utcnow().isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Phase 2: canonical 1:1 structured field map
# ---------------------------------------------------------------------------
# Bump this whenever the canonical field set or normalisation logic changes; it
# is persisted alongside the mapping so downstream consumers can detect stale
# extractions and re-run them.
OCR_FIELD_MAP_VERSION = "2.0"

# The exact, ordered set of fields the ID → field mapping guarantees. Every key
# is always present (value None when the engine could not read it) so the
# mapping is a stable 1:1 contract rather than a variable-shape dict.
OCR_CANONICAL_FIELDS = (
    "document_type",
    "national_id", "serial_number",
    "first_name", "middle_name", "last_name",
    "date_of_birth", "gender", "nationality",
    "district_of_birth", "place_of_issue", "date_of_issue", "date_of_expiry",
)


def extract_fields_structured(files: list[tuple[str, str, bytes]], *,
                              db=None, document=None) -> dict:
    """Run OCR and return a *validated, 1:1* Kenyan-ID field map (plan §2.3).

    Unlike ``process_id_files`` (which returns whatever the engine happened to
    read), this guarantees the full canonical field set — ID number, serial,
    first/middle/last name, date of birth, district of birth, place of issue and
    date of issue — with every key present (None when unread) plus a per-field
    confidence score and a single aggregate confidence.

    When ``db`` and ``document`` (a ``ClientDocument``) are supplied, the result is
    persisted onto the document: ``ocr_field_mapping`` (JSONB), ``ocr_confidence``
    (aggregate 0-1) and ``ocr_version`` are set, and ``ocr_applied``/``ocr_text``
    are refreshed. Returns the structured payload either way.
    """
    raw = process_id_files(files)
    src_fields = raw.get("fields", {}) or {}
    src_conf = raw.get("confidence", {}) or {}

    field_map: dict = {}
    conf_map: dict = {}
    for key in OCR_CANONICAL_FIELDS:
        field_map[key] = src_fields.get(key)
        conf_map[key] = round(float(src_conf.get(key, 0.0)), 2) if src_fields.get(key) not in (None, "") else 0.0

    populated = [conf_map[k] for k in OCR_CANONICAL_FIELDS if field_map.get(k) not in (None, "")]
    aggregate = round(sum(populated) / len(populated), 4) if populated else 0.0
    completeness = round(len(populated) / len(OCR_CANONICAL_FIELDS), 2)

    payload = {
        "engine": raw.get("engine"),
        "engine_notes": raw.get("engine_notes", []),
        "files_processed": raw.get("files_processed", len(files)),
        "ocr_version": OCR_FIELD_MAP_VERSION,
        "fields": field_map,
        "field_confidence": conf_map,
        "confidence": aggregate,
        "completeness": completeness,
        "raw_text": raw.get("raw_text", ""),
        "extracted_at": datetime.utcnow().isoformat() + "Z",
    }

    if db is not None and document is not None:
        document.ocr_applied = True
        document.ocr_text = (raw.get("raw_text") or "")[:20000]
        document.ocr_field_mapping = field_map
        document.ocr_confidence = aggregate
        document.ocr_version = OCR_FIELD_MAP_VERSION
        db.commit()

    return payload
