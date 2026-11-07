"""Extract structured invoice data from an image using Google Gemini.

Flow: check image -> send image + JSON schema to Gemini -> parse reply
into an ``Invoice`` model. Any failure is raised as ``ExtractionError``
with a human-readable message; ``extract_invoice`` turns that into an
``ExtractionResult`` with ``success=False`` so callers never crash.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import find_dotenv, load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from .models import ExtractionResult, Invoice
from .validation import validate_invoice

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.5-flash"
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # Gemini inline request limit
SUPPORTED_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
REQUEST_TIMEOUT_MS = 60_000  # per request; avoids hanging forever on a stuck connection
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

PROMPT = """You are an invoice data extraction system.
Read the attached invoice image and extract the data into the given JSON schema.

Rules:
- Copy text values exactly as printed (invoice number, descriptions, names).
- Numbers: digits and decimal point only. No thousands separators, no currency symbols.
- Quantity: the number only; put the unit (pcs, hrs, lot, ...) in the `unit` field.
- Dates: ISO format YYYY-MM-DD.
- Currency: ISO 4217 code (e.g. NPR, USD).
- Include every row of the items table, in order.
- Use the totals PRINTED on the invoice. Do not recalculate or correct them.
- If a value is not present or not readable, use null. Never guess or invent data.
- If the image is not an invoice, return all fields as null and items as an empty list.
"""


class ExtractionError(Exception):
    """Raised when an invoice cannot be extracted. Message is user-facing."""


def load_image(path: str | Path) -> tuple[bytes, str]:
    """Read and verify an image file. Returns (bytes, mime_type)."""
    path = Path(path)
    if not path.exists():
        raise ExtractionError(f"File not found: {path}")
    if not path.is_file():
        raise ExtractionError(f"Not a file: {path}")

    size = path.stat().st_size
    if size == 0:
        raise ExtractionError(f"File is empty: {path}")
    if size > MAX_IMAGE_BYTES:
        raise ExtractionError(
            f"Image too large ({size / 1_048_576:.1f} MB). Maximum is {MAX_IMAGE_BYTES // 1_048_576} MB."
        )

    # Detect the real format from file contents, not the extension.
    try:
        with Image.open(path) as img:
            fmt = img.format
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ExtractionError(f"Not a valid image file: {path} ({exc})") from exc

    if fmt not in SUPPORTED_FORMATS:
        supported = ", ".join(SUPPORTED_FORMATS)
        raise ExtractionError(f"Unsupported image format '{fmt}'. Supported: {supported}.")

    return path.read_bytes(), SUPPORTED_FORMATS[fmt]


def _parse_response(text: str | None) -> Invoice:
    """Turn the model's JSON text into an Invoice."""
    if not text or not text.strip():
        raise ExtractionError("AI returned an empty response (it may have been blocked or failed to read the image).")

    cleaned = text.strip()
    # Defensive: strip markdown code fences if the model added them.
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"AI returned invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ExtractionError(f"AI returned unexpected JSON type: {type(data).__name__}")

    try:
        return Invoice.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise ExtractionError(f"AI response did not match the invoice schema: {problems}") from exc


class GeminiExtractor:
    """Sends an invoice image to Gemini and returns a parsed Invoice."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_retries: int = 3,
        client: genai.Client | None = None,
    ) -> None:
        load_dotenv(find_dotenv(usecwd=True))
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        self.max_retries = max_retries

        if client is not None:  # allows tests to inject a fake client
            self.client = client
            return

        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "your-key-here":
            raise ExtractionError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key "
                "(free at https://aistudio.google.com/apikey)."
            )
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )

    def extract(self, image_path: str | Path) -> Invoice:
        image_bytes, mime_type = load_image(image_path)

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=Invoice.model_json_schema(),
            temperature=0,
        )
        contents = [types.Part.from_bytes(data=image_bytes, mime_type=mime_type), PROMPT]

        response = self._call_with_retry(contents, config)
        return _parse_response(response.text)

    def _call_with_retry(self, contents, config):
        for attempt in range(1, self.max_retries + 1):
            try:
                return self.client.models.generate_content(model=self.model, contents=contents, config=config)
            except genai_errors.APIError as exc:
                retryable = exc.code in RETRYABLE_STATUS
                reason = f"Gemini API error {exc.code}: {exc.message or exc}"
            except (httpx.TransportError, ConnectionError, TimeoutError) as exc:
                retryable = True
                reason = f"Network error: {exc}"

            if not retryable or attempt == self.max_retries:
                raise ExtractionError(f"{reason} (after {attempt} attempt(s))")

            wait = 2**attempt  # 2s, 4s, ...
            log.warning("%s - retrying in %ss (attempt %s/%s)", reason, wait, attempt, self.max_retries)
            time.sleep(wait)

        raise ExtractionError("Extraction failed for an unknown reason.")  # unreachable


def extract_invoice(
    image_path: str | Path,
    extractor: GeminiExtractor | None = None,
    model: str | None = None,
) -> ExtractionResult:
    """High-level entry point. Never raises: errors are reported in the result."""
    started = datetime.now(timezone.utc)
    model_name = model
    try:
        extractor = extractor or GeminiExtractor(model=model)
        model_name = extractor.model
        invoice = extractor.extract(image_path)
    except ExtractionError as exc:
        return ExtractionResult(
            source_file=str(image_path), model=model_name, extracted_at=started, success=False, error=str(exc)
        )
    except Exception as exc:  # last-resort safety net
        log.exception("Unexpected error during extraction")
        return ExtractionResult(
            source_file=str(image_path),
            model=model_name,
            extracted_at=started,
            success=False,
            error=f"Unexpected error: {type(exc).__name__}: {exc}",
        )

    return ExtractionResult(
        source_file=str(image_path),
        model=model_name,
        extracted_at=started,
        success=True,
        invoice=invoice,
        validation=validate_invoice(invoice),
    )
