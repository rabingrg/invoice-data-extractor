from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_extractor.models import Invoice, LineItem, Tax


def test_empty_invoice_has_all_fields_none():
    inv = Invoice.model_validate({})
    assert inv.invoice_number is None
    assert inv.total_amount is None
    assert inv.items == []


def test_null_items_becomes_empty_list():
    assert Invoice.model_validate({"items": None}).items == []


@pytest.mark.parametrize(
    "raw, expected",
    [("1,250.00", "1250.00"), ("NPR 113,565.00", "113565.00"), (5000, "5000"), (12.5, "12.5"), ("  7 ", "7")],
)
def test_money_parsing(raw, expected):
    item = LineItem(unit_price=raw)
    assert item.unit_price == Decimal(expected)


def test_percent_sign_is_stripped():
    assert Tax(rate_percent="13%").rate_percent == Decimal(13)


def test_empty_string_number_becomes_none():
    assert LineItem(quantity="").quantity is None


@pytest.mark.parametrize(
    "raw",
    ["2024-10-14", "14 October 2024", "14 Oct 2024", "October 14, 2024", "14/10/2024", "14-10-2024"],
)
def test_date_parsing(raw):
    assert Invoice(invoice_date=raw).invoice_date == date(2024, 10, 14)


def test_bad_number_raises_validation_error():
    with pytest.raises(ValidationError):
        LineItem(quantity="abc")


def test_bad_date_raises_validation_error():
    with pytest.raises(ValidationError):
        Invoice(invoice_date="yesterday")


def test_json_output_uses_iso_dates_and_plain_numbers(good_invoice):
    data = good_invoice.model_dump(mode="json")
    assert data["invoice_date"] == "2024-10-14"
    assert data["total_amount"] == 113565.0
    assert isinstance(data["items"][0]["quantity"], float)
