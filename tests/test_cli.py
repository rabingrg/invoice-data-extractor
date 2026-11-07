import json
from datetime import datetime, timezone

import pytest

from invoice_extractor import cli
from invoice_extractor.models import ExtractionResult, Invoice
from invoice_extractor.validation import validate_invoice


def make_result(data=None, error=None) -> ExtractionResult:
    now = datetime.now(timezone.utc)
    if error:
        return ExtractionResult(source_file="x.png", extracted_at=now, success=False, error=error)
    invoice = Invoice.model_validate(data)
    return ExtractionResult(
        source_file="x.png", model="fake", extracted_at=now, success=True,
        invoice=invoice, validation=validate_invoice(invoice),
    )


@pytest.fixture
def fake_extract(monkeypatch):
    def install(result):
        monkeypatch.setattr(cli, "extract_invoice", lambda path, model=None: result)
    return install


def test_exit_0_and_json_on_stdout(fake_extract, good_data, capsys):
    fake_extract(make_result(good_data))
    assert cli.main(["x.png", "--compact"]) == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["invoice"]["invoice_number"] == "RT-2024-1052"  # stdout is pure JSON
    assert "VALID | RT-2024-1052 | 5 items | NPR 113,565.00" in err


def test_exit_1_when_validation_fails(fake_extract, good_data, capsys):
    good_data["total_amount"] = 1
    fake_extract(make_result(good_data))
    assert cli.main(["x.png"]) == 1
    err = capsys.readouterr().err
    assert "INVALID" in err
    assert "error:" in err


def test_exit_2_when_extraction_fails(fake_extract, capsys):
    fake_extract(make_result(error="File not found: x.png"))
    assert cli.main(["x.png"]) == 2
    assert "FAILED: File not found" in capsys.readouterr().err


def test_output_file_written(fake_extract, good_data, tmp_path, capsys):
    fake_extract(make_result(good_data))
    out_file = tmp_path / "nested" / "out.json"
    assert cli.main(["x.png", "-o", str(out_file)]) == 0
    assert json.loads(out_file.read_text())["success"] is True
    out, err = capsys.readouterr()
    assert out == ""  # nothing on stdout when writing a file
    assert "Saved to" in err


def test_unwritable_output_path_exits_2(fake_extract, good_data, tmp_path, capsys):
    fake_extract(make_result(good_data))
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert cli.main(["x.png", "-o", str(blocker / "out.json")]) == 2
    assert "could not write" in capsys.readouterr().err


def test_warnings_shown_in_summary(fake_extract, good_data, capsys):
    good_data["due_date"] = "2024-11-20"
    fake_extract(make_result(good_data))
    assert cli.main(["x.png"]) == 0
    assert "warning:" in capsys.readouterr().err


def test_missing_argument_exits_2():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_model_flag_is_passed_through(monkeypatch, good_data):
    seen = {}

    def fake(path, model=None):
        seen["model"] = model
        return make_result(good_data)

    monkeypatch.setattr(cli, "extract_invoice", fake)
    cli.main(["x.png", "--model", "gemini-test"])
    assert seen["model"] == "gemini-test"
