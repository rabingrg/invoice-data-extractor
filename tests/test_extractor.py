import json

import pytest
from PIL import Image

from invoice_extractor.extractor import (
    ExtractionError,
    GeminiExtractor,
    _parse_response,
    extract_invoice,
    load_image,
)

from .conftest import FakeClient, api_error


def make_extractor(*replies, max_retries=3):
    client = FakeClient(*replies)
    return GeminiExtractor(client=client, max_retries=max_retries), client


# ---- image loading ---------------------------------------------------------

def test_load_image_ok(image_path):
    data, mime = load_image(image_path)
    assert mime == "image/png"
    assert data


def test_load_image_missing_file(tmp_path):
    with pytest.raises(ExtractionError, match="File not found"):
        load_image(tmp_path / "nope.png")


def test_load_image_directory(tmp_path):
    with pytest.raises(ExtractionError, match="Not a file"):
        load_image(tmp_path)


def test_load_image_empty_file(tmp_path):
    path = tmp_path / "empty.png"
    path.write_bytes(b"")
    with pytest.raises(ExtractionError, match="empty"):
        load_image(path)


def test_load_image_not_an_image(tmp_path):
    path = tmp_path / "fake.png"
    path.write_text("not an image")
    with pytest.raises(ExtractionError, match="Not a valid image"):
        load_image(path)


def test_load_image_unsupported_format(tmp_path):
    path = tmp_path / "x.gif"
    Image.new("RGB", (5, 5)).save(path)
    with pytest.raises(ExtractionError, match="Unsupported image format 'GIF'"):
        load_image(path)


def test_load_image_format_detected_from_content_not_extension(tmp_path):
    path = tmp_path / "photo.png"  # really a JPEG
    Image.new("RGB", (5, 5)).save(path, format="JPEG")
    assert load_image(path)[1] == "image/jpeg"


def test_load_image_too_large(image_path, monkeypatch):
    monkeypatch.setattr("invoice_extractor.extractor.MAX_IMAGE_BYTES", 10)
    with pytest.raises(ExtractionError, match="too large"):
        load_image(image_path)


# ---- API key ---------------------------------------------------------------

@pytest.mark.parametrize("key", ["", "your-key-here"])
def test_missing_api_key(monkeypatch, tmp_path, key):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("GEMINI_API_KEY", key)
    with pytest.raises(ExtractionError, match="GEMINI_API_KEY is not set"):
        GeminiExtractor()


# ---- response parsing ------------------------------------------------------

@pytest.mark.parametrize("text", [None, "", "   "])
def test_parse_empty_response(text):
    with pytest.raises(ExtractionError, match="empty response"):
        _parse_response(text)


def test_parse_invalid_json():
    with pytest.raises(ExtractionError, match="invalid JSON"):
        _parse_response("not json")


def test_parse_wrong_json_type():
    with pytest.raises(ExtractionError, match="unexpected JSON type"):
        _parse_response("[1, 2]")


def test_parse_schema_mismatch():
    with pytest.raises(ExtractionError, match="did not match the invoice schema.*subtotal"):
        _parse_response('{"subtotal": "abc"}')


def test_parse_strips_code_fences():
    inv = _parse_response('```json\n{"invoice_number": "X"}\n```')
    assert inv.invoice_number == "X"


# ---- extraction + retries --------------------------------------------------

def test_extract_success(image_path, good_data):
    extractor, client = make_extractor(json.dumps(good_data))
    inv = extractor.extract(image_path)
    assert inv.invoice_number == "RT-2024-1052"
    assert len(inv.items) == 5
    assert client.calls == 1


def test_retry_on_503_then_success(image_path, good_data):
    extractor, client = make_extractor(api_error(503), api_error(429), json.dumps(good_data))
    assert extractor.extract(image_path).invoice_number == "RT-2024-1052"
    assert client.calls == 3


def test_no_retry_on_client_error(image_path):
    extractor, client = make_extractor(api_error(403))
    with pytest.raises(ExtractionError, match="403"):
        extractor.extract(image_path)
    assert client.calls == 1


def test_gives_up_after_max_retries(image_path):
    extractor, client = make_extractor(api_error(503), api_error(503), api_error(503))
    with pytest.raises(ExtractionError, match=r"503.*after 3 attempt"):
        extractor.extract(image_path)
    assert client.calls == 3


def test_retry_on_network_error(image_path, good_data):
    extractor, client = make_extractor(ConnectionError("boom"), json.dumps(good_data))
    assert extractor.extract(image_path).invoice_number == "RT-2024-1052"
    assert client.calls == 2


# ---- extract_invoice never raises ------------------------------------------

def test_extract_invoice_success_includes_validation(image_path, good_data):
    extractor, _ = make_extractor(json.dumps(good_data))
    result = extract_invoice(image_path, extractor=extractor)
    assert result.success
    assert result.error is None
    assert result.validation.is_valid


def test_extract_invoice_reports_validation_errors(image_path, good_data):
    good_data["total_amount"] = 1
    extractor, _ = make_extractor(json.dumps(good_data))
    result = extract_invoice(image_path, extractor=extractor)
    assert result.success
    assert not result.validation.is_valid


def test_extract_invoice_bad_file_returns_failure(tmp_path):
    extractor, client = make_extractor()
    result = extract_invoice(tmp_path / "nope.png", extractor=extractor)
    assert not result.success
    assert "File not found" in result.error
    assert result.invoice is None
    assert client.calls == 0


def test_extract_invoice_api_failure_returns_failure(image_path):
    extractor, _ = make_extractor(api_error(500), api_error(500), api_error(500))
    result = extract_invoice(image_path, extractor=extractor)
    assert not result.success
    assert "500" in result.error


def test_extract_invoice_unexpected_exception_is_caught(image_path):
    extractor, _ = make_extractor(RuntimeError("surprise"))
    result = extract_invoice(image_path, extractor=extractor)
    assert not result.success
    assert "RuntimeError" in result.error
