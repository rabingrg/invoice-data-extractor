import pytest

from invoice_extractor.models import Invoice
from invoice_extractor.validation import validate_invoice


def run(data: dict):
    return validate_invoice(Invoice.model_validate(data))


def codes(issues):
    return [i.code for i in issues]


def test_real_invoice_is_valid(good_data):
    report = run(good_data)
    assert report.is_valid
    assert report.errors == []
    assert report.warnings == []


def test_wrong_total(good_data):
    good_data["total_amount"] = 115000
    report = run(good_data)
    assert not report.is_valid
    assert codes(report.errors) == ["TOTAL_MISMATCH"]
    assert "115,000.00" in report.errors[0].message


def test_wrong_line_total_also_breaks_subtotal(good_data):
    good_data["items"][1]["line_total"] = 9500
    report = run(good_data)
    assert codes(report.errors) == ["LINE_TOTAL_MISMATCH", "SUBTOTAL_MISMATCH"]
    assert report.errors[0].field == "items[1].line_total"


def test_wrong_vat_amount(good_data):
    good_data["tax"]["amount"] = 13000
    assert "TAX_MISMATCH" in codes(run(good_data).errors)


def test_missing_required_fields(good_data):
    good_data.update(invoice_number=None, invoice_date=None, supplier=None, total_amount=None)
    report = run(good_data)
    assert not report.is_valid
    assert {i.field for i in report.errors} == {"invoice_number", "invoice_date", "supplier.name", "total_amount"}


def test_no_items_is_error(good_data):
    good_data["items"] = []
    report = run(good_data)
    assert not report.is_valid
    assert any(i.field == "items" for i in report.errors)


def test_empty_invoice_reports_errors_without_crashing():
    report = run({})
    assert not report.is_valid
    assert len(report.errors) == 5


def test_item_with_missing_price_is_warning_not_error(good_data):
    good_data["items"][0]["unit_price"] = None
    report = run(good_data)
    assert report.is_valid
    assert codes(report.warnings) == ["ITEM_NOT_CHECKED"]


def test_negative_value_is_error(good_data):
    good_data["items"][4].update(unit_price=-5000, line_total=-5000)
    assert "NEGATIVE_VALUE" in codes(run(good_data).errors)


def test_missing_optional_fields_are_warnings(good_data):
    good_data.update(currency=None, due_date=None)
    good_data["supplier"]["phone"] = None
    report = run(good_data)
    assert report.is_valid
    assert {i.field for i in report.warnings} == {"currency", "due_date", "supplier.phone"}


def test_due_date_before_invoice_date(good_data):
    good_data["due_date"] = "2024-10-01"
    report = run(good_data)
    assert report.is_valid
    assert codes(report.warnings) == ["DUE_BEFORE_INVOICE"]


def test_due_date_disagrees_with_payment_terms(good_data):
    good_data["due_date"] = "2024-11-20"
    assert codes(run(good_data).warnings) == ["DUE_DATE_TERMS_MISMATCH"]


def test_unparseable_payment_terms_are_ignored(good_data):
    good_data["payment_terms"] = "On receipt"
    good_data["due_date"] = "2024-11-20"
    assert run(good_data).warnings == []


@pytest.mark.parametrize("total, valid", [("113565.004", True), ("113565.01", True), ("113565.02", False)])
def test_rounding_tolerance(good_data, total, valid):
    good_data["total_amount"] = total
    assert run(good_data).is_valid is valid


def test_missing_subtotal_falls_back_to_item_sum(good_data):
    good_data["subtotal"] = None
    report = run(good_data)
    assert report.is_valid
    assert "MISSING_OPTIONAL" in codes(report.warnings)

    good_data["total_amount"] = 120000
    assert codes(run(good_data).errors) == ["TOTAL_MISMATCH"]


def test_invoice_without_tax(good_data):
    good_data["tax"] = None
    good_data["total_amount"] = 100500
    assert run(good_data).is_valid
