"""Data models for extracted invoice data.

Every invoice field is optional: if the AI cannot find a value on the
invoice it returns ``null`` instead of guessing, and the validation step
(see ``validation.py``) reports what is missing.

Money and quantities use ``Decimal`` so totals can be checked exactly,
without floating-point rounding errors. In JSON output they are written
as plain numbers.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

# Decimal internally, plain JSON number in output.
Number = Annotated[
    Decimal,
    PlainSerializer(lambda v: float(v), return_type=float, when_used="json"),
]

# Date formats tolerated if the AI does not return ISO (YYYY-MM-DD).
_DATE_FORMATS = ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%d/%m/%Y", "%d-%m-%Y")


def _to_decimal(value: Any) -> Any:
    """Accept numbers written like '1,250.00' or 'NPR 5,000'."""
    if value is None or isinstance(value, (Decimal, int, float)):
        return value
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("NPR", "").replace("%", "").strip()
        if cleaned == "":
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            raise ValueError(f"not a valid number: {value!r}")
    return value


def _to_date(value: Any) -> Any:
    """Accept ISO dates plus a few common human-written formats."""
    if value is None or isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"unrecognised date format: {value!r}")
    return value


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class Supplier(_Base):
    name: str | None = Field(None, description="Company name of the seller issuing the invoice.")
    address: str | None = Field(None, description="Full postal address of the supplier, one line.")
    phone: str | None = Field(None, description="Supplier phone number as printed.")
    email: str | None = Field(None, description="Supplier email address.")


class BillTo(_Base):
    name: str | None = Field(None, description="Name of the customer being billed.")
    address: str | None = Field(None, description="Customer address, one line, including department if shown.")


class LineItem(_Base):
    description: str | None = Field(None, description="Item or service description exactly as printed.")
    quantity: Number | None = Field(None, description="Numeric quantity only, without the unit (e.g. 50).")
    unit: str | None = Field(None, description="Unit of the quantity, e.g. 'pcs', 'hrs', 'lot'.")
    unit_price: Number | None = Field(None, description="Price per unit, number only, no commas or currency.")
    line_total: Number | None = Field(None, description="Total amount for this line as printed on the invoice.")

    _numbers = field_validator("quantity", "unit_price", "line_total", mode="before")(_to_decimal)


class Tax(_Base):
    label: str | None = Field(None, description="Tax name as printed, e.g. 'VAT'.")
    rate_percent: Number | None = Field(None, description="Tax rate as a percentage number, e.g. 13 for 13%.")
    amount: Number | None = Field(None, description="Tax amount as printed.")

    _numbers = field_validator("rate_percent", "amount", mode="before")(_to_decimal)


class Invoice(_Base):
    invoice_number: str | None = Field(None, description="Invoice number / ID exactly as printed.")
    invoice_date: date | None = Field(None, description="Invoice issue date in ISO format YYYY-MM-DD.")
    due_date: date | None = Field(None, description="Payment due date in ISO format YYYY-MM-DD.")
    payment_terms: str | None = Field(None, description="Payment terms as printed, e.g. '30 Days'.")
    currency: str | None = Field(None, description="ISO 4217 currency code, e.g. 'NPR'.")
    supplier: Supplier | None = Field(None, description="The company that issued the invoice.")
    bill_to: BillTo | None = Field(None, description="The customer the invoice is addressed to.")
    items: list[LineItem] = Field(default_factory=list, description="Every line item in the invoice table, in order.")
    subtotal: Number | None = Field(None, description="Subtotal before tax as printed.")
    tax: Tax | None = Field(None, description="Tax / VAT applied to the subtotal.")
    total_amount: Number | None = Field(None, description="Final total amount payable as printed.")

    _numbers = field_validator("subtotal", "total_amount", mode="before")(_to_decimal)
    _dates = field_validator("invoice_date", "due_date", mode="before")(_to_date)

    @field_validator("items", mode="before")
    @classmethod
    def _none_items_to_empty(cls, value: Any) -> Any:
        return [] if value is None else value


class ValidationIssue(_Base):
    code: str = Field(description="Machine-readable issue code, e.g. 'TOTAL_MISMATCH'.")
    message: str = Field(description="Human-readable explanation.")
    field: str | None = Field(None, description="Path of the field concerned, e.g. 'items[2].line_total'.")


class ValidationReport(_Base):
    is_valid: bool = True
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class ExtractionResult(_Base):
    """Top-level output written to JSON."""

    source_file: str
    model: str | None = None
    extracted_at: datetime
    success: bool
    invoice: Invoice | None = None
    validation: ValidationReport | None = None
    error: str | None = Field(None, description="Set when extraction failed; explains why.")
