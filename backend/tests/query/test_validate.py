"""Cypher allowlist: labels, relationships, write/admin clauses, procedures.

Part of the query suite split out of the former ``tests/test_query_agent.py``.
"""

from __future__ import annotations

from app.query.validate import validate_cypher


def test_allowlist_rejects_disallowed_label():
    violations = validate_cypher("MATCH (u:User) RETURN u")
    assert any("User" in v for v in violations)


def test_allowlist_rejects_disallowed_relationship():
    violations = validate_cypher("MATCH (a:Ticker)-[:OWNED_BY]->(b:Politician) RETURN a")
    assert any("OWNED_BY" in v for v in violations)


def test_allowlist_rejects_write_clauses():
    for statement in [
        "MERGE (t:Ticker {ticker: 'NVDA'})",
        "MATCH (t:Ticker {ticker: 'NVDA'}) SET t.x = 1",
        "MATCH (t:Ticker) DELETE t",
        "CREATE (e:Event {id: 'x'})",
    ]:
        assert validate_cypher(statement), f"expected rejection: {statement}"


def test_allowlist_rejects_admin_and_planning_clauses_issues_15():
    """SHOW/USE/EXPLAIN/PROFILE are read-ish but leak schema or execution
    plans; the model must not be able to reach them."""
    for statement in [
        "SHOW CONSTRAINTS",
        "SHOW INDEXES YIELD name RETURN name",
        "USE system SHOW DATABASES",
        "EXPLAIN MATCH (t:Ticker) RETURN t",
        "PROFILE MATCH (t:Ticker) RETURN t",
    ]:
        assert validate_cypher(statement), f"expected rejection: {statement}"


def test_allowlist_accepts_read_only_query():
    query = (
        "MATCH (c:Committee {id: $id})-[:OVERSEES]->(s:Sector)<-[:IN_SECTOR]-(t:Ticker) "
        "RETURN s.name AS sector, collect(DISTINCT t.ticker) AS tickers"
    )
    assert validate_cypher(query) == []


def test_allowlist_rejects_procedure_calls():
    assert validate_cypher("CALL db.labels() YIELD label RETURN label")


def test_allowlist_rejects_load_csv():
    """LOAD CSV reads the server's local filesystem; a crafted question could
    otherwise prompt the model to exfiltrate file contents."""
    assert validate_cypher("LOAD CSV WITH HEADERS FROM 'file:///etc/passwd' AS row RETURN row")


def test_allowlist_rejects_multi_statement():
    """`;` separates statements in Neo4j; only a single trailing one is the
    model's statement terminator, anything else is statement injection."""
    assert validate_cypher("MATCH (t:Ticker) RETURN t; MATCH (e:Event) RETURN e")


def test_allowlist_tolerates_single_trailing_semicolon():
    assert validate_cypher("MATCH (t:Ticker) RETURN t;") == []


def test_allowlist_rejects_index_constraint_statements():
    """Index/constraint DDL is caught by the dedicated pattern check (beyond
    the generic CREATE clause block)."""
    for query in (
        "CREATE INDEX idx FOR (n:Event) ON (n.id)",
        "CREATE CONSTRAINT c FOR (n:Event) REQUIRE n.id IS UNIQUE",
    ):
        violations = validate_cypher(query)
        assert any("index/constraint" in v for v in violations)


def test_direction_check_rejects_reversed_labeled_edges():
    """Live failure: (p:Politician)-[:INVOLVES_POLITICIAN]->(e:Event) runs
    clean and returns empty. The gate must flag the direction."""
    violations = validate_cypher("MATCH (p:Politician)-[:INVOLVES_POLITICIAN]->(e:Event) RETURN p")
    assert any("direction" in v for v in violations)


def test_direction_check_accepts_schema_edges_with_properties():
    ok = validate_cypher(
        "MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker {ticker: 'NVDA'}) "
        "MATCH (t)-[:IN_SECTOR]->(s:Sector) RETURN s.name"
    )
    assert ok == []
    leftward = validate_cypher("MATCH (e:Event)-[:INVOLVES_POLITICIAN]->(p:Politician {id: 'W000817'}) RETURN p")
    assert leftward == []


def test_direction_check_rejects_wrong_pairs_and_ignores_unlabeled():
    violations = validate_cypher("MATCH (t:Ticker)-[:IN_SECTOR]->(p:Politician) RETURN t")
    assert any("direction" in v for v in violations)
    assert validate_cypher("MATCH (a)-[:IN_SECTOR]->(b) RETURN a") == []
    assert validate_cypher("MATCH (a:Ticker)-[:IN_SECTOR]-(s:Sector) RETURN a") == []


def test_repair_flips_reversed_schema_edges_only():
    from app.query.validate import repair_edge_directions

    q = (
        "MATCH (p:Politician)-[:INVOLVES_POLITICIAN]->(e:Event)<-[:INVOLVES_TICKER]-(t:Ticker)"
        "-[:IN_SECTOR]->(s:Sector) RETURN s.name"
    )
    fixed, notes = repair_edge_directions(q)
    assert fixed == (
        "MATCH (p:Politician)<-[:INVOLVES_POLITICIAN]-(e:Event)-[:INVOLVES_TICKER]->(t:Ticker)"
        "-[:IN_SECTOR]->(s:Sector) RETURN s.name"
    )
    assert len(notes) == 2
    assert validate_cypher(fixed) == []
    # Correct, unlabeled and undirected patterns are left alone.
    for ok in [
        "MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker) RETURN t",
        "MATCH (a)-[:INVOLVES_TICKER]->(b) RETURN a",
        "MATCH (t:Ticker)-[:INVOLVES_TICKER]-(e:Event) RETURN count(e)",
    ]:
        assert repair_edge_directions(ok) == (ok, [])
