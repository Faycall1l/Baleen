"""EDGAR Form 4 feed tests: Atom parsing, XML parsing, grounding."""

from __future__ import annotations

from datetime import date, datetime, timezone

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
    assert filing["owners"] == [
        {"cik": "1212502", "name": "AULT MILTON C III", "role": "Executive Chairman"}
    ]
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
    result = src.fetch_recent([], 86400)

    assert result.errors == []
    assert len(result.tweets) == 1
    tweet = result.tweets[0]
    assert tweet.tweet_id.startswith("edgar-0001214659-26-011648")
    assert "AULT MILTON C III" in tweet.text and "$GPUS" in tweet.text
    assert tweet.raw_json["source_kind"] == SOURCE_KIND
    assert tweet.raw_json["insider_cik"] == "1212502"


def test_source_skips_seen_filings_on_repill():
    src = EdgarForm4Source(fetch_text=_fetcher())
    assert len(src.fetch_recent([], 86400).tweets) == 1
    assert src.fetch_recent([], 86400).tweets == []


def test_source_fetch_failure_returns_errors():
    def boom(url: str):
        raise RuntimeError("SEC down")

    result = EdgarForm4Source(fetch_text=boom).fetch_recent([], 3600)
    assert result.tweets == []
    assert len(result.errors) == 1


def _tweet() -> RawTweet:
    return RawTweet(
        tweet_id="edgar-x", author_handle="edgar_form4", author_id="edgar_form4",
        text="INSIDER FILING: AULT MILTON C III (Executive Chairman), GPUS",
        created_at=datetime.now(timezone.utc),
        url="https://www.sec.gov/Archives/edgar/data/1/2-index.htm",
        raw_json={
            "source_kind": SOURCE_KIND, "filer_name": "AULT MILTON C III",
            "insider_cik": "1212502", "role": "Executive Chairman",
            "ticker": "GPUS", "transaction_type": "buy",
            "shares": 1000.0, "price": 2.5, "amount_range_label": "$2,500.00",
            "transaction_date": "2026-09-09",
        },
    )


def _event() -> InsiderTradeEvent:
    return InsiderTradeEvent(
        tweet_id="edgar-x", tweet_url="https://www.sec.gov/x", confidence=0.9,
        insider_name="Wrong Name", insider_id="WRONG", ticker="WRONG",
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
        tweet_id="edgar-x", tweet_url="https://www.sec.gov/x", confidence=0.9,
        ticker="GPUS", contract_type="call",
    )
    assert maybe_enrich_insider(_tweet(), options) is options
