# Invoice Extractor

A small Python application that reads an invoice image, extracts the key
information with an AI model (Google Gemini), validates it, and outputs
structured JSON.

```
invoice image ──▶ Gemini (vision + JSON schema) ──▶ parsed Invoice ──▶ validation ──▶ JSON
```

## What it extracts

| Field | JSON key |
|---|---|
| Invoice number | `invoice_number` |
| Invoice date | `invoice_date` (ISO `YYYY-MM-DD`) |
| Supplier name | `supplier.name` (plus address, phone, email) |
| Items | `items[]` with `description`, `quantity`, `unit`, `unit_price`, `line_total` |
| Total amount | `total_amount` |

It also captures due date, payment terms, currency, bill-to, subtotal and
VAT, because they are needed to validate the total.

## Requirements

- Python 3.10 or newer (developed and tested on 3.13)
- A Google Gemini API key. Create one at <https://aistudio.google.com/apikey>.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install the app (add [dev] to also install pytest)
pip install -e ".[dev]"

# 3. Add your API key
cp .env.example .env               # then edit .env and set GEMINI_API_KEY
```

## Run

```bash
# Print JSON to the screen
invoice-extract samples/invoice.png

# Save JSON to a file
invoice-extract samples/invoice.png -o output.json

# Same thing without installing the command
python -m invoice_extractor samples/invoice.png
```

Options:

| Option | Meaning |
|---|---|
| `-o, --output FILE` | Write JSON to a file instead of the screen |
| `--model NAME` | Gemini model to use (default `gemini-3.5-flash`, or the `GEMINI_MODEL` env var) |
| `--compact` | Single-line JSON instead of pretty-printed |
| `-v, --verbose` | Show debug logs |

JSON goes to **stdout** (or the `-o` file). A short human-readable summary goes
to **stderr**, so the JSON can be piped into other tools:

```
Extracting samples/invoice.png ...
VALID | RT-2024-1052 | 5 items | NPR 113,565.00
```

**Exit codes:** `0` extracted and valid, `1` extracted but failed validation,
`2` extraction failed (bad file, missing key, API error).

## Example output

The full result for the provided invoice is in
[`samples/output.json`](samples/output.json). Abridged:

```json
{
  "source_file": "samples/invoice.png",
  "model": "gemini-3.5-flash",
  "success": true,
  "invoice": {
    "invoice_number": "RT-2024-1052",
    "invoice_date": "2024-10-14",
    "due_date": "2024-11-13",
    "currency": "NPR",
    "supplier": { "name": "RIVERTECH SUPPLIES LTD.", "email": "info@rivertech.com.np" },
    "items": [
      { "description": "HDPE Pipe 110mm (6m length)", "quantity": 50.0, "unit": "pcs",
        "unit_price": 1250.0, "line_total": 62500.0 }
    ],
    "subtotal": 100500.0,
    "tax": { "label": "VAT", "rate_percent": 13.0, "amount": 13065.0 },
    "total_amount": 113565.0
  },
  "validation": { "is_valid": true, "errors": [], "warnings": [] },
  "error": null
}
```

## Validation

Run automatically after every extraction and reported in the `validation` block.

**Errors** (`is_valid` becomes `false`, exit code 1):

- each item: `quantity × unit_price` equals `line_total`
- sum of line totals equals `subtotal`
- `subtotal × VAT rate` equals the VAT amount
- `subtotal + VAT` equals `total_amount`
- invoice number, invoice date, supplier name, at least one item and the total are present
- no negative values

**Warnings** (reported, invoice stays valid):

- optional fields missing (currency, due date, supplier contact details)
- an item lacks a value so its line total cannot be checked
- due date is before the invoice date
- due date does not match the payment terms (e.g. "30 Days")

Money comparisons allow a difference of 0.01 for rounding. If the subtotal is
missing, the total is checked against the sum of the line items instead.
Each issue has a `code`, a readable `message` and the `field` it concerns.

## Error handling

The app never crashes on bad input. Failures come back as
`"success": false` with an explanation in `"error"`, and exit code 2:

- file missing, empty, a directory, not really an image, unsupported format, over 20 MB
- missing or placeholder `GEMINI_API_KEY`
- API errors: rate limits (429), server errors (5xx) and network errors are
  retried up to 3 times with a growing wait; other errors (e.g. 403) fail
  immediately with Gemini's message
- each request has a 60 second timeout
- AI reply that is empty, invalid JSON, the wrong shape, or has unparseable
  numbers or dates
- missing information on the invoice becomes `null` (and an empty `items` list),
  never a guessed value

## Design choices

- **Structured output.** The `Invoice` schema (Pydantic) is sent to Gemini as the
  required JSON schema, so the reply is JSON in the right shape instead of free
  text that needs parsing. The same models parse and check the reply.
- **Prompt rules.** The model is told to copy values exactly as printed, use
  `null` when unsure, use ISO dates, return numbers without separators, and
  **not** recalculate totals. Validation is done in code, not by the AI, so a
  wrong printed total is caught rather than silently "fixed". Temperature is 0.
- **`Decimal` for money.** No floating-point rounding errors in the checks;
  values are written to JSON as plain numbers.
- **Lenient parsing.** Values like `"1,250.00"`, `"NPR 5,000"`, `"13%"` or
  `"14 October 2024"` are accepted if the model returns them that way.
- **Format from content.** The image type is detected from the file bytes, not
  the extension.
- **Testable.** The Gemini client can be injected, so tests run with a scripted
  fake and need no network.

## Project layout

```
src/invoice_extractor/
  models.py       Pydantic models (Invoice, LineItem, ValidationReport, ...)
  extractor.py    Image checks, Gemini call, retries, response parsing
  validation.py   Business-rule checks
  cli.py          Command-line interface
tests/            pytest suite (no network, no API key needed)
samples/          invoice.png and a real output.json
```

## Tests

```bash
pytest
```

71 tests covering parsing, every validation rule, image checks, retries,
error replies and CLI exit codes. They use a fake Gemini client, so they need no
API key and cost nothing.

## Limitations

- Reads one invoice image per run (PNG, JPEG, WEBP). PDFs are not supported;
  convert them to an image first.
- Built and tested against the one sample invoice layout. Other layouts should
  work, but accuracy on poor scans or handwriting is not guaranteed.
- Extraction depends on the Gemini API: it needs internet access and a key, and
  model availability and free-tier limits are set by Google. If a model returns
  "no longer available" or "high demand", try another with `--model` or
  `GEMINI_MODEL`. Some Google Cloud projects have no free tier; creating the
  key in a new project in AI Studio fixed this during development.
- Invoice data is sent to Google's Gemini API. Do not use real confidential
  invoices unless that is acceptable for your data policy.
- Amounts are assumed to be in a single currency.
