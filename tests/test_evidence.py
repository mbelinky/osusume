from datetime import datetime, timezone

import pytest

from osusume.evidence import Claim, ClaimLedger, ClaimStatus, EvidenceRecord, SourceClass, classify_source


NOW = datetime(2026, 8, 26, 10, tzinfo=timezone.utc)
FRESHNESS = {
    "operational_status": 0,
    "hours_at_arrival": 0,
    "detour": 0,
    "product_inventory": 548,
    "counter_service": 548,
    "layout": 548,
    "quality": 1095,
    "generic": 548,
}


def ledger_for(claim_type: str, records: list[EvidenceRecord]) -> ClaimLedger:
    ledger = ClaimLedger()
    ledger.add_claim(Claim("claim", "Claim text", claim_type, required=True))
    for record in records:
        ledger.add_evidence(record)
    ledger.freeze()
    return ledger


def judgments(records: list[EvidenceRecord], *, invented: bool = False) -> list[dict]:
    return [
        {
            "claim_id": "claim",
            "evidence_id": row.evidence_id,
            "quote": "invented quote" if invented else row.quote,
            "entails": row.polarity == "supports",
            "contradicts": row.polarity == "contradicts",
            "status": "supported",
        }
        for row in records
    ]


def evidence(evidence_id: str, kind: str, date: str, *, url: str = "https://example.com/page", polarity: str = "supports", roundup: bool = False) -> EvidenceRecord:
    return EvidenceRecord(evidence_id, "claim", kind, url, "2026-08-26T10:00:00+00:00", date, "literal evidence text", "literal evidence", polarity, roundup)


def test_never_load_bearing_and_roundup_tables() -> None:
    assert classify_source("caller_note") == SourceClass.NEVER
    assert classify_source("delivery_app") == SourceClass.NEVER
    assert classify_source("qualified_guide", roundup=True) == SourceClass.MENTION


def test_stale_current_state_cannot_be_upgraded_by_judge() -> None:
    rows = [evidence("old", "official_site", "2016-06-01T00:00:00+00:00")]
    ledger = ledger_for("product_inventory", rows)
    ledger.compute(judgments(rows), FRESHNESS, now=NOW)
    assert ledger.claims[0].status == ClaimStatus.STALE


def test_layout_rejects_reviews_and_delivery_even_with_high_weights() -> None:
    rows = [
        evidence("delivery", "delivery_app", "2026-08-20T00:00:00+00:00", url="https://glovoapp.com/x"),
        evidence("review", "review", "2026-08-20T00:00:00+00:00", url="https://tripadvisor.com/x"),
    ]
    ledger = ledger_for("counter_service", rows)
    ledger.compute(judgments(rows), FRESHNESS, now=NOW)
    assert ledger.claims[0].status == ClaimStatus.WRONG_SOURCE


def test_two_independent_review_domains_can_support_product_claim() -> None:
    one = [evidence("a", "review", "2026-08-20T00:00:00+00:00", url="https://one.example/a")]
    ledger = ledger_for("product_inventory", one)
    ledger.compute(judgments(one), FRESHNESS, now=NOW)
    assert ledger.claims[0].status == ClaimStatus.UNKNOWN

    two = one + [evidence("b", "review", "2026-08-20T00:00:00+00:00", url="https://two.test/b")]
    ledger = ledger_for("product_inventory", two)
    ledger.compute(judgments(two), FRESHNESS, now=NOW)
    assert ledger.claims[0].status == ClaimStatus.SUPPORTED


def test_conflicting_qualified_evidence_blocks_claim() -> None:
    rows = [
        evidence("yes", "official_site", "2026-08-20T00:00:00+00:00"),
        evidence("no", "photo", "2026-08-20T00:00:00+00:00", polarity="contradicts"),
    ]
    ledger = ledger_for("layout", rows)
    ledger.compute(judgments(rows), FRESHNESS, now=NOW)
    assert ledger.claims[0].status == ClaimStatus.CONFLICT


def test_invented_quote_is_dropped_loudly() -> None:
    rows = [evidence("a", "official_site", "2026-08-20T00:00:00+00:00")]
    ledger = ledger_for("layout", rows)
    ledger.compute(judgments(rows, invented=True), FRESHNESS, now=NOW)
    assert ledger.claims[0].status == ClaimStatus.UNKNOWN
    assert ledger.claims[0].drop_count == 1


def test_frozen_ledger_rejects_new_claims_and_evidence() -> None:
    ledger = ledger_for("layout", [])
    with pytest.raises(RuntimeError, match="frozen"):
        ledger.add_claim(Claim("late", "Late", "layout"))
    with pytest.raises(RuntimeError, match="frozen"):
        ledger.add_evidence(evidence("late", "photo", "2026-08-20T00:00:00+00:00"))
