"""Business-rule validation for extracted invoices.

Errors mean the data is inconsistent or incomplete (``is_valid = False``).
Warnings flag things worth a human look but do not fail the invoice.
"""

from __future__ import annotations

import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from .models import Invoice, ValidationIssue, ValidationReport

# Allowed difference for money comparisons (one paisa / cent).
TOLERANCE = Decimal("0.01")
CENT = Decimal("0.01")

_PAYMENT_TERMS_DAYS = re.compile(r"(\d+)\s*days?|net\s*(\d+)", re.IGNORECASE)


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    return f"{_money(value):,.2f}"


def _matches(expected: Decimal, actual: Decimal) -> bool:
    return abs(_money(expected) - _money(actual)) <= TOLERANCE


def _payment_terms_days(terms: str | None) -> int | None:
    if not terms:
        return None
    match = _PAYMENT_TERMS_DAYS.search(terms)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


class _Collector:
    def __init__(self) -> None:
        self.errors: list[ValidationIssue] = []
        self.warnings: list[ValidationIssue] = []

    def error(self, code: str, message: str, field: str | None = None) -> None:
        self.errors.append(ValidationIssue(code=code, message=message, field=field))

    def warn(self, code: str, message: str, field: str | None = None) -> None:
        self.warnings.append(ValidationIssue(code=code, message=message, field=field))

    def report(self) -> ValidationReport:
        return ValidationReport(is_valid=not self.errors, errors=self.errors, warnings=self.warnings)


def _check_required(inv: Invoice, c: _Collector) -> None:
    if not inv.invoice_number:
        c.error("MISSING_FIELD", "Invoice number is missing.", "invoice_number")
    if inv.invoice_date is None:
        c.error("MISSING_FIELD", "Invoice date is missing.", "invoice_date")
    if inv.supplier is None or not inv.supplier.name:
        c.error("MISSING_FIELD", "Supplier name is missing.", "supplier.name")
    if not inv.items:
        c.error("MISSING_FIELD", "No line items were found.", "items")
    if inv.total_amount is None:
        c.error("MISSING_FIELD", "Total amount is missing.", "total_amount")

    # Optional, but useful to know about.
    if not inv.currency:
        c.warn("MISSING_OPTIONAL", "Currency is missing.", "currency")
    if inv.due_date is None:
        c.warn("MISSING_OPTIONAL", "Due date is missing.", "due_date")
    if inv.subtotal is None:
        c.warn("MISSING_OPTIONAL", "Subtotal is missing; subtotal checks skipped.", "subtotal")
    if inv.supplier is not None:
        for name in ("address", "phone", "email"):
            if not getattr(inv.supplier, name):
                c.warn("MISSING_OPTIONAL", f"Supplier {name} is missing.", f"supplier.{name}")


def _check_items(inv: Invoice, c: _Collector) -> None:
    for i, item in enumerate(inv.items):
        path = f"items[{i}]"
        label = item.description or f"item {i + 1}"

        if not item.description:
            c.warn("MISSING_OPTIONAL", f"Item {i + 1} has no description.", f"{path}.description")

        missing = [n for n in ("quantity", "unit_price", "line_total") if getattr(item, n) is None]
        if missing:
            c.warn(
                "ITEM_NOT_CHECKED",
                f"'{label}': cannot check line total, missing {', '.join(missing)}.",
                path,
            )
            continue

        for name in ("quantity", "unit_price", "line_total"):
            if getattr(item, name) < 0:
                c.error("NEGATIVE_VALUE", f"'{label}': {name} is negative.", f"{path}.{name}")

        expected = item.quantity * item.unit_price
        if not _matches(expected, item.line_total):
            c.error(
                "LINE_TOTAL_MISMATCH",
                f"'{label}': {item.quantity} x {_fmt(item.unit_price)} = {_fmt(expected)}, "
                f"but invoice shows {_fmt(item.line_total)}.",
                f"{path}.line_total",
            )


def _check_totals(inv: Invoice, c: _Collector) -> None:
    line_totals = [item.line_total for item in inv.items]
    tax_amount = inv.tax.amount if inv.tax else None
    tax_rate = inv.tax.rate_percent if inv.tax else None

    # 1. Sum of line totals == subtotal
    if inv.subtotal is not None and line_totals:
        if any(t is None for t in line_totals):
            c.warn("SUBTOTAL_NOT_CHECKED", "Some line totals are missing; subtotal sum not checked.", "subtotal")
        else:
            items_sum = sum(line_totals, Decimal(0))
            if not _matches(items_sum, inv.subtotal):
                c.error(
                    "SUBTOTAL_MISMATCH",
                    f"Line items add up to {_fmt(items_sum)}, but subtotal shows {_fmt(inv.subtotal)}.",
                    "subtotal",
                )

    # 2. Subtotal x rate == tax amount
    if inv.subtotal is not None and tax_rate is not None and tax_amount is not None:
        expected_tax = inv.subtotal * tax_rate / Decimal(100)
        if not _matches(expected_tax, tax_amount):
            c.error(
                "TAX_MISMATCH",
                f"{_fmt(inv.subtotal)} x {tax_rate}% = {_fmt(expected_tax)}, but tax shows {_fmt(tax_amount)}.",
                "tax.amount",
            )
    elif inv.tax is not None and (tax_rate is None or tax_amount is None):
        c.warn("TAX_NOT_CHECKED", "Tax rate or amount is missing; tax calculation not checked.", "tax")

    # 3. Subtotal + tax == total
    if inv.total_amount is None:
        return
    base = inv.subtotal
    if base is None and line_totals and all(t is not None for t in line_totals):
        base = sum(line_totals, Decimal(0))  # fall back to item sum
    if base is None:
        c.warn("TOTAL_NOT_CHECKED", "No subtotal or complete line totals; total not checked.", "total_amount")
        return

    expected_total = base + (tax_amount or Decimal(0))
    if not _matches(expected_total, inv.total_amount):
        tax_part = f" + tax {_fmt(tax_amount)}" if tax_amount is not None else ""
        c.error(
            "TOTAL_MISMATCH",
            f"Subtotal {_fmt(base)}{tax_part} = {_fmt(expected_total)}, "
            f"but total shows {_fmt(inv.total_amount)}.",
            "total_amount",
        )


def _check_dates(inv: Invoice, c: _Collector) -> None:
    if inv.invoice_date is None or inv.due_date is None:
        return
    if inv.due_date < inv.invoice_date:
        c.warn("DUE_BEFORE_INVOICE", "Due date is before the invoice date.", "due_date")
        return

    days = _payment_terms_days(inv.payment_terms)
    if days is not None:
        expected_due = inv.invoice_date + timedelta(days=days)
        if expected_due != inv.due_date:
            c.warn(
                "DUE_DATE_TERMS_MISMATCH",
                f"Payment terms '{inv.payment_terms}' imply due date {expected_due.isoformat()}, "
                f"but invoice shows {inv.due_date.isoformat()}.",
                "due_date",
            )


def validate_invoice(invoice: Invoice) -> ValidationReport:
    """Run all checks and return a report. Never raises."""
    c = _Collector()
    _check_required(invoice, c)
    _check_items(invoice, c)
    _check_totals(invoice, c)
    _check_dates(invoice, c)
    return c.report()
