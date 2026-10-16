"""EDGAR Form 4 feed tests: Atom parsing, XML parsing, grounding."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.ingestion.edgar import (
    SOURCE_KIND,
    EdgarForm4Source,
    enrich_insider_trade,
    maybe_enrich_insider,
    parse_atom,
    parse_ownership,
)
from app.ingestion.source import RawTweet
from app.models.events import InsiderTradeEvent, OptionsFlowEvent

ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<updated>2026-09-12T07:01:56-04:00</updated>
<entry>
<title>4 - AULT MILTON C III (0001212502) (Reporting)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/1212502/000121465926011648/0001214659-26-011648-index.htm"/>
<updated>2026-09-11T21:59:18-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0001214659-26-011648</id>
</entry>
<entry>
<title>4 - No Link Corp (0009999999) (Reporting)</title>
<updated>2026-09-11T20:00:00-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=0009999999-26-000001</id>
</entry>
</feed>"""

INDEX_HTML = """
<html><body><table>
<tr><td><a href="/Archives/edgar/data/896493/000121465926011648/xslF345X06/doc4.xml">doc4.xml</a></td></tr>
<tr><td><a href="/Archives/edgar/data/896493/000121465926011648/doc4.xml">doc4.xml</a></td></tr>
</table></body></html>
"""

OWNERSHIP_XML = """<?xml version="1.0"?>
<ownershipDocument>
<issuer><issuerCik>0000896493</issuerCik><issuerName>Hyperscale Data, Inc.</issuerName>
<issuerTradingSymbol>GPUS</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0001212502</rptOwnerCik>
<rptOwnerName>AULT MILTON C III</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>false</isDirector><isOfficer>true</isOfficer>
<officerTitle>Executive Chairman</officerTitle><isTenPercentOwner>true</isTenPercentOwner>
</reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction>
<securityTitle><value>Common Stock</value></securityTitle>
<transactionDate><value>2026-09-09</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares>
<transactionPricePerShare><value>2.5</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
</transactionAmounts></nonDerivativeTransaction>
<nonDerivativeTransaction>
<securityTitle><value>Common Stock</value></securityTitle>
<transactionDate><value>2026-09-09</value></transactionDate>
<transactionCoding><transactionFormType>4</transactionFormType><transactionCode>G</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>50</value></transactionShares>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
</transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable>
</ownershipDocument>"""


def test_parse_atom_extracts_filings():
    entries = parse_atom(ATOM)

    assert len(entries) == 1  # linkless entry skipped
    assert entries[0]["accession"] == "0001214659-26-011648"
    assert entries[0]["index_url"].endswith("-index.htm")


def test_parse_ownership_extracts_open_market_only():
    filing = parse_ownership(OWNERSHIP_XML)

    assert filing["ticker"] == "GPUS"
    assert filing["owners"] == [{"cik": "1212502", "name": "AULT MILTON C III", "role": "Executive Chairman"}]
    assert len(filing["transactions"]) == 1  # gift (G) skipped
    txn = filing["transactions"][0]
    assert (txn["side"], txn["shares"], txn["price"]) == ("buy", 1000.0, 2.5)
    assert txn["transaction_date"] == date(2026, 9, 9)


def _fetcher():
    def fetch(url: str) -> str:
        if "output=atom" in url or "getcurrent" in url:
            return ATOM
        if url.endswith("-index.htm"):
            return INDEX_HTML
        return OWNERSHIP_XML

    return fetch


def test_source_emits_one_record_per_trade():
    src = EdgarForm4Source(fetch_text=_fetcher())
    result = src.fetch_recent([], 86400 * 365)

    assert result.errors == []
    assert len(result.tweets) == 1
    tweet = result.tweets[0]
    assert tweet.tweet_id.startswith("edgar-0001214659-26-011648")
    assert "AULT MILTON C III" in tweet.text and "$GPUS" in tweet.text
    assert tweet.raw_json["source_kind"] == SOURCE_KIND
    assert tweet.raw_json["insider_cik"] == "1212502"


def test_source_skips_seen_filings_on_repill():
    src = EdgarForm4Source(fetch_text=_fetcher())
    assert len(src.fetch_recent([], 86400 * 365).tweets) == 1
    assert src.fetch_recent([], 86400 * 365).tweets == []


def test_source_fetch_failure_returns_errors():
    def boom(url: str):
        raise RuntimeError("SEC down")

    result = EdgarForm4Source(fetch_text=boom).fetch_recent([], 3600)
    assert result.tweets == []
    assert len(result.errors) == 1


def test_transient_filing_failure_retried_next_poll_issues_12():
    calls = {"n": 0}

    def flaky(url: str):
        if "output=atom" in url or "getcurrent" in url:
            return ATOM
        if url.endswith("-index.htm"):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("SEC timeout")
            return INDEX_HTML
        return OWNERSHIP_XML

    src = EdgarForm4Source(fetch_text=flaky)
    first = src.fetch_recent([], 86400 * 365)
    assert first.tweets == [] and len(first.errors) == 1

    second = src.fetch_recent([], 86400 * 365)
    assert second.errors == []
    assert len(second.tweets) == 1
    assert calls["n"] == 2  # accession not skipped after the transient failure


def _tweet() -> RawTweet:
    return RawTweet(
        tweet_id="edgar-x",
        author_handle="edgar_form4",
        author_id="edgar_form4",
        text="INSIDER FILING: AULT MILTON C III (Executive Chairman), GPUS",
        created_at=datetime.now(UTC),
        url="https://www.sec.gov/Archives/edgar/data/1/2-index.htm",
        raw_json={
            "source_kind": SOURCE_KIND,
            "filer_name": "AULT MILTON C III",
            "insider_cik": "1212502",
            "role": "Executive Chairman",
            "ticker": "GPUS",
            "transaction_type": "buy",
            "shares": 1000.0,
            "price": 2.5,
            "amount_range_label": "$2,500.00",
            "transaction_date": "2026-09-09",
        },
    )


def _event() -> InsiderTradeEvent:
    return InsiderTradeEvent(
        tweet_id="edgar-x",
        tweet_url="https://www.sec.gov/x",
        confidence=0.9,
        insider_name="Wrong Name",
        insider_id="WRONG",
        ticker="WRONG",
        transaction_type="sell",
    )


def test_enrichment_grounds_cik_and_facts():
    enriched = enrich_insider_trade(_tweet(), _event())

    assert enriched.insider_id == "1212502"
    assert enriched.insider_name == "AULT MILTON C III"
    assert enriched.ticker == "GPUS"
    assert enriched.transaction_type == "buy"
    assert enriched.transaction_date == date(2026, 9, 9)
    assert enriched.amount_range == "$2,500.00"
    assert enriched.confidence == 0.9


def test_enrichment_ignores_placeholder_filing_ticker():
    """A filing ticker of NONE must not clobber the event's ticker."""
    tweet = _tweet()
    tweet.raw_json = {**tweet.raw_json, "ticker": "NONE"}
    enriched = enrich_insider_trade(tweet, _event())

    assert enriched.ticker == "WRONG"


def test_enrichment_unknown_filer_gets_slug():
    tweet = _tweet()
    tweet.raw_json = dict(tweet.raw_json, insider_cik=None, filer_name="Jane Unknown")
    enriched = enrich_insider_trade(tweet, _event())

    assert enriched.insider_id == "slug-jane-unknown"
    assert enriched.insider_name == "Jane Unknown"


def test_maybe_enrich_is_noop_without_marker_or_other_types():
    tweet = _tweet()
    tweet.raw_json = {}
    event = _event()
    assert maybe_enrich_insider(tweet, event) is event

    options = OptionsFlowEvent(
        tweet_id="edgar-x",
        tweet_url="https://www.sec.gov/x",
        confidence=0.9,
        ticker="GPUS",
        contract_type="call",
    )
    assert maybe_enrich_insider(_tweet(), options) is options


def test_maybe_enrich_keeps_model_output_when_enrichment_raises(monkeypatch):
    """A crash inside enrichment must not surface: the model's own output is
    kept so the event still flows through extraction/pipeline."""
    import app.ingestion.edgar as edgar

    def boom(tweet, event):
        raise RuntimeError("grounding exploded")

    monkeypatch.setattr(edgar, "enrich_insider_trade", boom)
    tweet = _tweet()
    event = _event()

    assert maybe_enrich_insider(tweet, event) is event


def test_custom_user_agent_sent_to_sec(monkeypatch):
    import httpx

    from app.ingestion.edgar import EdgarForm4Source

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        @property
        def text(self):
            return ""

    def fake_get(url, **kwargs):
        seen.update(kwargs.get("headers") or {})
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    src = EdgarForm4Source(user_agent="Mine/1.0 (contact: me@x.com)")
    src._download("https://www.sec.gov/x")

    assert seen.get("User-Agent") == "Mine/1.0 (contact: me@x.com)"


def test_edgar_user_agent_setting_exists():
    from app.config import Settings

    assert "contact:" in Settings(_env_file=None).edgar_user_agent


# ---------------------------------------------------------------------------
# Deterministic extractor helpers (defensive branches)
# ---------------------------------------------------------------------------


def test_parse_atom_rejects_malformed_xml():
    import pytest

    from app.ingestion.edgar_parse import parse_atom

    with pytest.raises(ValueError, match="unparseable Atom"):
        parse_atom("<<<< not xml")


def test_parse_atom_skips_entries_without_accession_or_link():
    from app.ingestion.edgar_parse import parse_atom

    entries = parse_atom(ATOM)
    assert len(entries) == 1  # the link-less "No Link Corp" entry is dropped
    assert entries[0]["accession"] == "0001214659-26-011648"
    assert "index.htm" in entries[0]["index_url"]


def test_parse_ownership_rejects_malformed_xml():
    import pytest

    from app.ingestion.edgar_parse import parse_ownership

    with pytest.raises(ValueError, match="unparseable ownership"):
        parse_ownership("<<<< not xml")


def test_parse_ownership_skips_owner_without_name():
    from app.ingestion.edgar_parse import parse_ownership

    xml = """<ownershipDocument><issuer><issuerCik>1</issuerCik>
    <issuerName>X Corp</issuerName><issuerTradingSymbol>XXX</issuerTradingSymbol>
    </issuer><reportingOwner><reportingOwnerId><rptOwnerCik>2</rptOwnerCik>
    </reportingOwnerId></reportingOwner></ownershipDocument>"""
    parsed = parse_ownership(xml)
    assert parsed["owners"] == []
    assert parsed["ticker"] == "XXX"


def test_text_flag_number_and_day_helpers():
    import xml.etree.ElementTree as ET

    from app.ingestion.edgar_parse import _flag, _number, _parse_day, _text

    assert _text(None, "x") is None
    root = ET.fromstring("<r><a>  hello  </a><b /></r>")
    assert _text(root, "a") == "hello"
    assert _text(root, "b") is None  # empty element -> None

    assert _flag("1") is True and _flag("YES") is True and _flag("nope") is False and _flag(None) is False

    assert _number("1,200.5") == 1200.5
    assert _number(None) is None
    assert _number("n/a") is None

    assert _parse_day("2026-09-09T00:00:00") == date(2026, 9, 9)
    assert _parse_day("garbage") is None
    assert _parse_day(None) is None


def test_owner_role_orders_multiple_flags():
    import xml.etree.ElementTree as ET

    from app.ingestion.edgar_parse import _owner_role

    owner = ET.fromstring(
        "<o><reportingOwnerRelationship><isOfficer>true</isOfficer>"
        "<officerTitle>CFO</officerTitle>"
        "<isDirector>true</isDirector><isTenPercentOwner>true</isTenPercentOwner>"
        "</reportingOwnerRelationship></o>"
    )
    assert _owner_role(owner) == "CFO"
    assert _owner_role(ET.fromstring("<o />")) is None


def test_owner_role_lists_flags_without_officer_title():
    """No officer title: the roles are joined in a fixed order (no title
    overrides), and the empty-role case returns None."""
    import xml.etree.ElementTree as ET

    from app.ingestion.edgar_parse import _owner_role

    owner = ET.fromstring(
        "<o><reportingOwnerRelationship>"
        "<isDirector>true</isDirector><isTenPercentOwner>true</isTenPercentOwner>"
        "<isOfficer>true</isOfficer><officerTitle/></reportingOwnerRelationship></o>"
    )
    assert _owner_role(owner) == "Director, 10% Owner, Officer"

    quiet = ET.fromstring(
        "<o><reportingOwnerRelationship><isDirector>false</isDirector>"
        "<isTenPercentOwner>no</isTenPercentOwner><isOfficer></isOfficer>"
        "</reportingOwnerRelationship></o>"
    )
    assert _owner_role(quiet) is None


def test_parse_ownership_skips_unknown_acquired_code():
    """An open-market transaction coded outside {A, D} is dropped from the
    parsed rows (the G/Gift row is already excluded by the code gate)."""
    from app.ingestion.edgar_parse import parse_ownership

    modified = OWNERSHIP_XML.replace(
        """<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>""",
        """<transactionAcquiredDisposedCode><value>X</value></transactionAcquiredDisposedCode>""",
    )
    assert parse_ownership(modified)["transactions"] == []


def test_xml_href_prefers_plain_document_and_none_without_xml():
    from app.ingestion.edgar_parse import _xml_href

    assert _xml_href('<a href="/Archives/x/doc4.xml">x</a>') == "https://www.sec.gov/Archives/x/doc4.xml"
    assert _xml_href('<a href="https://ok/doc4.xml">x</a>') == "https://ok/doc4.xml"
    assert _xml_href("<p>no anchor here</p>") is None


def test_fmt_amount_requires_shares_and_price():
    from app.ingestion.edgar_parse import _fmt_amount

    assert _fmt_amount(1000, 2.5) == "$2,500.00"
    assert _fmt_amount(None, 2.5) is None
    assert _fmt_amount(1000, None) is None


def test_record_requires_ticker_and_owner_name():
    from app.ingestion.edgar_parse import _record

    now = datetime.now(UTC)
    owner = {"name": "Ada Lovelace", "role": ""}
    txn = {"side": "buy", "shares": 100, "price": 25.0, "transaction_date": date(2026, 9, 9)}

    record = _record("r1", "https://x", now, owner, txn, "JPM", now)
    assert record is not None
    assert "100 shares @ $25.00" in record.text
    assert _record("r2", "https://x", now, owner, txn, "", now) is None
    assert _record("r3", "https://x", now, {"name": ""}, txn, "JPM", now) is None


def test_placeholder_trading_symbols_become_missing_tickers():
    """Filers put NONE/N/A in issuerTradingSymbol when there is no listed
    symbol (seen live). That must read as missing — never as a "NONE"
    Ticker node in the graph."""
    from app.ingestion.edgar_parse import clean_ticker, parse_ownership

    assert clean_ticker("NONE") == ""
    assert clean_ticker(" n/a ") == ""
    assert clean_ticker("UNKNOWN") == ""
    assert clean_ticker("NVDA") == "NVDA"
    assert clean_ticker("") == ""
    assert clean_ticker(None) == ""

    xml = """<ownershipDocument><issuer><issuerCik>1</issuerCik>
    <issuerName>Some Plan</issuerName><issuerTradingSymbol>NONE</issuerTradingSymbol>
    </issuer><reportingOwner><reportingOwnerId><rptOwnerCik>2</rptOwnerCik>
    <rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId></reportingOwner>
    <nonDerivativeTable><nonDerivativeTransaction>
    <securityTitle><value>Common Stock</value></securityTitle>
    <transactionDate><value>2026-09-28</value></transactionDate>
    <transactionCoding><transactionFormType>4</transactionFormType>
    <transactionCode>P</transactionCode>
    <equitySwapInvolved><value>0</value></equitySwapInvolved></transactionCoding>
    <transactionAmounts><transactionShares><value>10</value></transactionShares>
    <transactionPricePerShare><value>5</value></transactionPricePerShare>
    <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
    </transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
    assert parse_ownership(xml)["ticker"] == ""


def test_parse_ts_tolerates_garbage():
    from app.ingestion.edgar import _parse_ts

    assert _parse_ts(None) == 0.0
    assert _parse_ts(12345) == 0.0
    assert _parse_ts("not-a-date") == 0.0
    assert _parse_ts("2026-09-11T21:59:18-04:00") == datetime.fromisoformat("2026-09-11T21:59:18-04:00").timestamp()


def test_filing_records_raise_without_ownership_xml_link():
    import pytest

    from app.ingestion.edgar import EdgarForm4Source

    src = EdgarForm4Source(fetch_text=lambda url: "<html>no documents</html>")
    with pytest.raises(ValueError, match="no ownership XML link"):
        src._filing_records(
            {"index_url": "https://www.sec.gov/i", "accession": "1", "updated": "x"},
            datetime.now(UTC),
        )


def test_enrich_insider_trade_tolerates_invalid_dates():
    """A malformed transaction_date in the filing meta must not crash
    enrichment; other authoritative fields still overwrite the model's."""
    from app.ingestion.edgar import enrich_insider_trade

    tweet = _tweet().model_copy(
        update={
            "raw_json": {**_tweet().raw_json, "transaction_date": "not-a-date"},
        }
    )
    grounded = enrich_insider_trade(tweet, _event())

    assert grounded.ticker == "GPUS"  # filed facts still win
    assert grounded.insider_id == "1212502"
    assert grounded.transaction_date is None  # invalid date never forces one
