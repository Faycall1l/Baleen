"""Hand-curated reference dataset for the Baleen demo (see docs/ASSUMPTIONS.md).

Contains US congressional committees, politician memberships, committee→sector
oversight mappings, and a ticker→sector map. Conceptually sourced from public
congressional committee assignments; the assignments below are a reasonable
hand-curated subset for demonstration. The seed script
(``scripts/seed_reference.py``) loads it into Neo4j with ``MERGE``, so it is
idempotent.

This data is *reference*, not extracted-from-tweets: it is what makes the query
agent and the ``politician_trades_in_oversight_sector`` alert rule meaningful.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Sectors
# ---------------------------------------------------------------------------

SECTORS: list[str] = [
    "Financial Services",
    "Technology",
    "Energy",
    "Healthcare",
    "Defense",
    "Communications",
    "Agriculture",
    "Transportation",
]

# ---------------------------------------------------------------------------
# Committees  (id -> display name)
# ---------------------------------------------------------------------------

COMMITTEES: dict[str, str] = {
    "HSBA": "House Financial Services Committee",
    "SSBK": "Senate Banking, Housing and Urban Affairs Committee",
    "SSHR": "Senate Health, Education, Labor and Pensions Committee",
    "HSIF": "House Energy and Commerce Committee",
    "SSEG": "Senate Energy and Natural Resources Committee",
    "HSAS": "House Armed Services Committee",
    "SSAS": "Senate Armed Services Committee",
    "HSAG": "House Agriculture Committee",
    "SSAF": "Senate Agriculture, Nutrition and Forestry Committee",
    "HSPW": "House Transportation and Infrastructure Committee",
}

# ---------------------------------------------------------------------------
# Committee -> sectors overseen
# ---------------------------------------------------------------------------

COMMITTEE_SECTORS: dict[str, list[str]] = {
    "HSBA": ["Financial Services"],
    "SSBK": ["Financial Services"],
    "SSHR": ["Healthcare"],
    "HSIF": ["Energy", "Healthcare", "Communications"],
    "SSEG": ["Energy"],
    "HSAS": ["Defense"],
    "SSAS": ["Defense"],
    "HSAG": ["Agriculture"],
    "SSAF": ["Agriculture"],
    "HSPW": ["Transportation"],
}

# ---------------------------------------------------------------------------
# Politicians  (id, name, party, chamber, committee ids)
# ---------------------------------------------------------------------------

POLITICIANS: list[dict] = [
    {"id": "W000187", "name": "Maxine Waters", "party": "D", "chamber": "house", "committees": ["HSBA"]},
    {"id": "M001156", "name": "Patrick McHenry", "party": "R", "chamber": "house", "committees": ["HSBA"]},
    {"id": "O000172", "name": "Alexandria Ocasio-Cortez", "party": "D", "chamber": "house", "committees": ["HSBA"]},
    {"id": "D000626", "name": "Warren Davidson", "party": "R", "chamber": "house", "committees": ["HSBA"]},
    {"id": "W000817", "name": "Elizabeth Warren", "party": "D", "chamber": "senate", "committees": ["SSBK", "SSHR"]},
    {"id": "B000944", "name": "Sherrod Brown", "party": "D", "chamber": "senate", "committees": ["SSBK"]},
    {"id": "S001184", "name": "Tim Scott", "party": "R", "chamber": "senate", "committees": ["SSBK"]},
    {"id": "K000393", "name": "John Kennedy", "party": "R", "chamber": "senate", "committees": ["SSBK"]},
    {"id": "C001098", "name": "Ted Cruz", "party": "R", "chamber": "senate", "committees": ["SSEG"]},
    {"id": "S000033", "name": "Bernie Sanders", "party": "I", "chamber": "senate", "committees": ["SSHR"]},
    {"id": "C000261", "name": "Bill Cassidy", "party": "R", "chamber": "senate", "committees": ["SSHR"]},
    {"id": "T000278", "name": "Tommy Tuberville", "party": "R", "chamber": "senate", "committees": ["SSAS"]},
    {"id": "R000122", "name": "Jack Reed", "party": "D", "chamber": "senate", "committees": ["SSAS"]},
    {"id": "T000467", "name": "Glenn Thompson", "party": "R", "chamber": "house", "committees": ["HSAG"]},
    {"id": "S001157", "name": "David Scott", "party": "D", "chamber": "house", "committees": ["HSAG"]},
    {"id": "M001177", "name": "Cathy McMorris Rodgers", "party": "R", "chamber": "house", "committees": ["HSIF"]},
    {"id": "P000034", "name": "Frank Pallone", "party": "D", "chamber": "house", "committees": ["HSIF"]},
    {"id": "M001183", "name": "Joe Manchin", "party": "D", "chamber": "senate", "committees": ["SSEG"]},
    {"id": "C000127", "name": "Maria Cantwell", "party": "D", "chamber": "senate", "committees": ["SSEG"]},
    {"id": "C001090", "name": "Dan Crenshaw", "party": "R", "chamber": "house", "committees": ["HSAS"]},
    {"id": "K000375", "name": "Rohit Khanna", "party": "D", "chamber": "house", "committees": ["HSAS"]},
    {"id": "G000583", "name": "Josh Gottheimer", "party": "D", "chamber": "house", "committees": ["HSBA"]},
    {"id": "M001232", "name": "April McClain Delaney", "party": "D", "chamber": "house", "committees": ["HSAG"]},
    {"id": "R000619", "name": "Michael Rulli", "party": "R", "chamber": "house", "committees": ["HSIF"]},
    {"id": "F000110", "name": "Cleo Fields", "party": "D", "chamber": "house", "committees": ["HSBA"]},
    {"id": "C001123", "name": "Gilbert Cisneros", "party": "D", "chamber": "house", "committees": ["HSAS"]},
    {"id": "B001288", "name": "Cory A Booker", "party": "D", "chamber": "senate", "committees": ["SSAF"]},
    {"id": "M001239", "name": "John J McGuire III", "party": "R", "chamber": "house", "committees": ["HSAS"]},
]

# ---------------------------------------------------------------------------
# Ticker -> sector
# ---------------------------------------------------------------------------

TICKER_SECTORS: dict[str, str] = {
    # Financial Services
    "JPM": "Financial Services",
    "BAC": "Financial Services",
    "GS": "Financial Services",
    "MS": "Financial Services",
    "C": "Financial Services",
    "WFC": "Financial Services",
    "SCHW": "Financial Services",
    # Technology
    "NVDA": "Technology",
    "AMD": "Technology",
    "INTC": "Technology",
    "MU": "Technology",
    "TSM": "Technology",
    "AVGO": "Technology",
    "AAPL": "Technology",
    "MSFT": "Technology",
    "GOOGL": "Technology",
    "META": "Technology",
    "CRM": "Technology",
    "ORCL": "Technology",
    "TSLA": "Technology",
    # Energy
    "XOM": "Energy",
    "CVX": "Energy",
    "SLB": "Energy",
    "OXY": "Energy",
    "COP": "Energy",
    # Healthcare
    "PFE": "Healthcare",
    "MRNA": "Healthcare",
    "UNH": "Healthcare",
    "LLY": "Healthcare",
    "JNJ": "Healthcare",
    "MRK": "Healthcare",
    "ABBV": "Healthcare",
    # Defense
    "LMT": "Defense",
    "RTX": "Defense",
    "NOC": "Defense",
    "GD": "Defense",
    "BA": "Defense",
    # Communications
    "T": "Communications",
    "VZ": "Communications",
    "CMCSA": "Communications",
    "DIS": "Communications",
    "NFLX": "Communications",
    # Agriculture
    "ADM": "Agriculture",
    "DE": "Agriculture",
    "BG": "Agriculture",
    "MOS": "Agriculture",
    # Transportation
    "UNP": "Transportation",
    "FDX": "Transportation",
    "UPS": "Transportation",
    "CSX": "Transportation",
    "DAL": "Transportation",
    "UAL": "Transportation",
}
