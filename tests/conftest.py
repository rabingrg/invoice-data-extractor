"""Shared fixtures: sample invoice data and a fake Gemini client (no network)."""

import copy
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from PIL import Image

from invoice_extractor import extractor as extractor_module
from invoice_extractor.models import Invoice

# The real sample invoice (samples/invoice.png) as the AI should return it.
GOOD_INVOICE = {
    "invoice_number": "RT-2024-1052",
    "invoice_date": "2024-10-14",
    "due_date": "2024-11-13",
    "payment_terms": "30 Days",
    "currency": "NPR",
    "supplier": {
        "name": "RIVERTECH SUPPLIES LTD.",
        "address": "45 Industrial Estate Road, Kathmandu 44600, Nepal",
        "phone": "+977 1 4567890",
        "email": "info@rivertech.com.np",
    },
    "bill_to": {"name": "Deutschelink GmbH", "address": "Project Department, Sanobharyang, Kathmandu 44700, Nepal"},
    "items": [
        {"description": "HDPE Pipe 110mm (6m length)", "quantity": 50, "unit": "pcs", "unit_price": 1250, "line_total": 62500},
        {"description": "HDPE Elbow 90° 110mm", "quantity": 20, "unit": "pcs", "unit_price": 450, "line_total": 9000},
        {"description": "HDPE Coupling 110mm", "quantity": 30, "unit": "pcs", "unit_price": 300, "line_total": 9000},
        {"description": "Welding Service", "quantity": 10, "unit": "hrs", "unit_price": 1500, "line_total": 15000},
        {"description": "Delivery Charge", "quantity": 1, "unit": "lot", "unit_price": 5000, "line_total": 5000},
    ],
    "subtotal": 100500,
    "tax": {"label": "VAT", "rate_percent": 13, "amount": 13065},
    "total_amount": 113565,
}


@pytest.fixture
def good_data() -> dict:
    """Fresh copy of valid invoice data that a test can mutate."""
    return copy.deepcopy(GOOD_INVOICE)


@pytest.fixture
def good_invoice(good_data) -> Invoice:
    return Invoice.model_validate(good_data)


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "invoice.png"
    Image.new("RGB", (20, 20), "white").save(path)
    return path


class FakeClient:
    """Stands in for genai.Client. Each call pops the next scripted reply.

    A reply is either response text (str) or an exception to raise.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0
        self.models = self  # client.models.generate_content(...)

    def generate_content(self, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(text=reply)


def api_error(code: int) -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"message": f"error {code}", "status": "X"}})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retries must not slow the test suite down."""
    monkeypatch.setattr(extractor_module.time, "sleep", lambda _s: None)
