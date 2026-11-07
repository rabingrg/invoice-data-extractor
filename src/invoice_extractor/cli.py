"""Command-line interface.

    python -m invoice_extractor samples/invoice.png -o output.json

JSON goes to stdout (or the -o file); a human-readable summary goes to
stderr, so stdout can be piped into other tools.

Exit codes: 0 = extracted and valid, 1 = extracted but failed validation,
2 = extraction failed (bad file, API error, ...).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .extractor import extract_invoice
from .models import ExtractionResult

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-extract",
        description="Extract structured data from an invoice image using Google Gemini.",
    )
    parser.add_argument("image", help="Path to invoice image (PNG, JPEG or WEBP).")
    parser.add_argument("-o", "--output", type=Path, help="Write JSON to this file instead of stdout.")
    parser.add_argument("--model", help="Gemini model name (default: GEMINI_MODEL env var or gemini-3.5-flash).")
    parser.add_argument("--compact", action="store_true", help="Write single-line JSON instead of pretty-printed.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug logs.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _summary(result: ExtractionResult) -> str:
    if not result.success:
        return f"FAILED: {result.error}"

    inv = result.invoice
    report = result.validation
    status = "VALID" if report is None or report.is_valid else "INVALID"
    total = f"{inv.currency or ''} {inv.total_amount:,.2f}".strip() if inv.total_amount is not None else "no total"
    lines = [f"{status} | {inv.invoice_number or 'no invoice number'} | {len(inv.items)} items | {total}"]

    if report:
        lines += [f"  error:   {issue.message}" for issue in report.errors]
        lines += [f"  warning: {issue.message}" for issue in report.warnings]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    if not args.verbose:
        # Keep third-party HTTP noise out of normal output.
        for noisy in ("httpx", "httpcore", "google_genai"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    print(f"Extracting {args.image} ...", file=sys.stderr)
    result = extract_invoice(args.image, model=args.model)
    payload = result.model_dump_json(indent=None if args.compact else 2)

    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(payload + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"FAILED: could not write {args.output}: {exc}", file=sys.stderr)
            return EXIT_ERROR
    else:
        print(payload)

    print(_summary(result), file=sys.stderr)
    if args.output:
        print(f"Saved to {args.output}", file=sys.stderr)

    if not result.success:
        return EXIT_ERROR
    if result.validation and not result.validation.is_valid:
        return EXIT_INVALID
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
