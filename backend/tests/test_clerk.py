"""Clerk-direct feed tests: index parsing, PDF parsing, source behavior."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ingestion.clerk import (
    ClerkHouseSource,
    parse_index,
    parse_ptr_pdf,
)

INDEX = (
    "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
    "\tAaron\tRichard\t\tW\tMI04\t2026\t4/15/2026\t8068\r\n"
    "\tTaylor\tDavid\t\tP\tOH02\t2026\t9/5/2026\t20035392\r\n"
    "\tSmith\tJane\t\tP\tCA12\t2026\t9/6/2026\t9116142\r\n"
    "\tBrown\tBob\t\tC\tTX01\t2026\t9/6/2026\t20039999\r\n"
)

PTR_TEXT = (
    "Name: Hon. David J. Taylor State/District:OH02 "
    "American Electric Power Company, Inc. (AEP) [ST] "
    "P 08/26/202609/03/2026$1,001 - $15,000 F S: New "
    "Amgen Inc. - Common Stock (AMGN) [ST] "
    "S 08/26/202609/03/2026$1,001 - $15,000 F S: New "
    "Broadcom Inc. - Common Stock (AVGO) [ST] "
    "P 08/26/202609/03/2026$1,001 - $15,000 F S: New "
    "Microsoft Corporation - Common Stock (MSFT) [ST] "
    "S 08/26/202609/03/2026$1,001 - $15,000 F S: New "
    "Mystery Fund (XYZ) [ST] "
    "E 08/26/202609/03/2026$1,001 - $15,000 F S: New "
    "Dave's IRA @ Schwab (Home Grown) Filing ID #20035392 "
    "I CERTIFY that the statements I have made on the attached Periodic "
    "Transaction Report are true, complete, and correct to the best of my "
    "knowledge and belief as required by the STOCK Act."
)


def _pdf_bytes(text: str) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = writer._add_object(font)
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources
    stream = DecodedStreamObject()
    chunks = [text[i : i + 100] for i in range(0, len(text), 100)] or [""]
    ops = ["BT /F1 12 Tf 72 720 Td"]
    for i, chunk in enumerate(chunks):
        escaped = chunk.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({escaped}) Tj")
        if i < len(chunks) - 1:
            ops.append("T*")
    ops.append("ET")
    stream.set_data(" ".join(ops).encode("latin-1", errors="replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    import io

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_parse_index_keeps_efiled_ptrs_only():
    filings = parse_index(INDEX)

    assert [f["doc_id"] for f in filings] == ["20035392"]
    assert filings[0]["filer_name"] == "David Taylor"
    assert filings[0]["state"] == "OH"
    assert filings[0]["filing_date"] == "2026-09-05"


def test_parse_ptr_pdf_extracts_trades_and_skips_exchanges():
    trades = parse_ptr_pdf(_pdf_bytes(PTR_TEXT))

    assert [(t["ticker"], t["transaction_type"]) for t in trades] == [
        ("AEP", "Purchase"),
        ("AMGN", "Sale"),
        ("AVGO", "Purchase"),
        ("MSFT", "Sale"),
    ]
    assert trades[0]["transaction_date"] == "2026-08-26"
    assert trades[0]["amount_range_label"] == "$1,001 - $15,000"


def test_parse_ptr_pdf_empty_means_scan():
    assert parse_ptr_pdf(_pdf_bytes("Name: Hon. X")) == []


def test_source_emits_traceable_records():
    src = ClerkHouseSource(
        year=2026,
        fetch_index=lambda year: INDEX,
        fetch_pdf=lambda year, doc_id: _pdf_bytes(PTR_TEXT),
    )
    result = src.fetch_recent([], 3650 * 86400)

    assert result.errors == []
    assert len(result.tweets) == 4
    first = result.tweets[0]
    assert first.tweet_id == "ptr-house_20035392_0"
    assert "Taylor" in first.text and "$AEP" in first.text
    assert first.url == "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035392.pdf"
    assert first.raw_json["source_kind"] == "congress_disclosure"
    assert first.raw_json["chamber"] == "house"


def test_source_skips_seen_filings_on_repill():
    src = ClerkHouseSource(
        year=2026,
        fetch_index=lambda year: INDEX,
        fetch_pdf=lambda year, doc_id: _pdf_bytes(PTR_TEXT),
    )
    assert len(src.fetch_recent([], 3650 * 86400).tweets) == 4
    assert src.fetch_recent([], 3650 * 86400).tweets == []  # same process: no re-download


def test_source_index_failure_returns_errors():
    def boom(year: int):
        raise RuntimeError("clerk down")

    result = ClerkHouseSource(fetch_index=boom).fetch_recent([], 3600)
    assert result.tweets == []
    assert len(result.errors) == 1


def test_transient_pdf_failure_retried_next_poll_issues_12():
    calls = {"n": 0}

    def flaky(year: int, doc_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("pdf timeout")
        return _pdf_bytes(PTR_TEXT)

    src = ClerkHouseSource(
        year=2026,
        fetch_index=lambda year: INDEX,
        fetch_pdf=flaky,
    )
    first = src.fetch_recent([], 3650 * 86400)
    assert first.tweets == [] and len(first.errors) == 1

    second = src.fetch_recent([], 3650 * 86400)
    assert second.errors == []
    assert len(second.tweets) == 4
    assert calls["n"] == 2  # filing not skipped after the transient failure


def test_parse_index_skips_short_lines_and_bad_dates():
    index = (
        "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
        "short-line-no-tabs\r\n"
        "\tShort\tRow\t\tP\tOH02\t2026\t\x00\t20035392\r\n"
        "\tTaylor\tDavid\t\tP\tOH02\t2026\tnotadate\t20035393\r\n"
        "\tValid\tRow\t\tP\tOH02\t2026\t9/5/2026\t20035399\r\n"
    )
    filings = parse_index(index)
    assert [f["doc_id"] for f in filings] == ["20035399"]


def test_parse_ptr_pdf_unreadable_bytes_return_empty():
    assert parse_ptr_pdf(b"this is definitely not a pdf") == []


def test_parse_ptr_pdf_skips_trade_with_invalid_transaction_date():
    text = (
        "Name: Hon. David J. Taylor State/District:OH02 "
        "Apple Inc. (AAPL) [ST] "
        "P 99/99/202609/03/2026$1,001 - $15,000 F S: New "
        + "padding to exceed the 500-byte scanned-form threshold. " * 10
    )
    trades = parse_ptr_pdf(_pdf_bytes(text))
    assert trades == []


def test_download_index_unzips_first_txt(monkeypatch):
    import io
    import zipfile

    import httpx

    from app.ingestion.clerk import ClerkHouseSource

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(
            "index.txt",
            "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
            "\tTaylor\tDavid\t\tP\tOH02\t2026\t9/5/2026\t20035392\r\n",
        )

    class _Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp(archive.getvalue())

    monkeypatch.setattr(httpx, "get", fake_get)
    src = ClerkHouseSource(year=2026)

    text = src._download_index(2026)

    assert "20035392" in text and "20035392".startswith("2")
    assert "2026FD.zip" in seen["url"]


def test_download_pdf_returns_bytes(monkeypatch):
    import httpx

    from app.ingestion.clerk import ClerkHouseSource

    class _Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp(b"%PDF-1.4 fake")

    monkeypatch.setattr(httpx, "get", fake_get)
    src = ClerkHouseSource(year=2026)

    assert src._download_pdf("2026", "20035392") == b"%PDF-1.4 fake"
    assert "20035392.pdf" in seen["url"]


def test_download_index_raises_when_zip_has_no_txt(monkeypatch):
    import io
    import zipfile

    import httpx

    from app.ingestion.clerk import ClerkHouseSource

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("cover.pdf", "not an index")

    class _Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda url, **kw: _Resp(archive.getvalue()))

    with pytest.raises(ValueError, match="no index .txt"):
        ClerkHouseSource(year=2026)._download_index(2026)


def test_malformed_record_skipped_without_aborting_poll(monkeypatch):
    """A row that crashes the record builder is logged and skipped; the rest of
    the poll (and the seen-set marking) still proceeds."""
    import app.ingestion.clerk as clerk

    def boom(row, now):
        raise ValueError("row malformed")

    monkeypatch.setattr(clerk, "_record", boom)
    src = ClerkHouseSource(
        year=2026,
        fetch_index=lambda year: INDEX,
        fetch_pdf=lambda year, doc_id: _pdf_bytes(PTR_TEXT),
    )

    result = src.fetch_recent([], 3650 * 86400)

    assert result.tweets == []
    assert result.errors == []  # a malformed row is not a source failure


def test_caps_pdfs_per_poll_then_breaks():
    # Two valid e-filed PTR filings inside the 30-day window (Smith's doc id
    # does not start with "2", so it is already dropped by parse_index).
    idx = INDEX + "\tZed\tZara\t\tP\tNY01\t2026\t9/6/2026\t20037777\r\n"
    src = ClerkHouseSource(
        year=2026,
        max_pdfs_per_poll=1,
        fetch_index=lambda year: idx,
        fetch_pdf=lambda year, doc_id: _pdf_bytes(PTR_TEXT),
    )
    result = src.fetch_recent([], 3650 * 86400)
    assert len(result.tweets) == 4  # only the top (newest) filing's trades


def test_index_short_line_no_crash():
    """Extra short lines in the raw index (blank / malformed trailing rows)
    are skipped; a single bad row must not kill the whole parse."""
    index = INDEX + "\t\n\n"
    filings = parse_index(index)
    assert all(f["doc_id"] for f in filings)


def test_today_is_used_for_window():
    today = datetime.now(timezone.utc).date()
    iso = today.isoformat()
    index = (
        "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\r\n"
        f"\tTaylor\tDavid\t\tP\tOH02\t{today.year}\t{today.month}/{today.day}/{today.year}\t20035392\r\n"
    )
    src = ClerkHouseSource(
        year=today.year,
        fetch_index=lambda year: index,
        fetch_pdf=lambda year, doc_id: _pdf_bytes(PTR_TEXT),
    )
    result = src.fetch_recent([], 3600)
    assert len(result.tweets) == 4
    assert result.tweets[0].created_at.date().isoformat() == iso
