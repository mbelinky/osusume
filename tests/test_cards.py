from pathlib import Path

import pytest
import yaml

from osusume.cards import CardValidationError, promote_card, save_ephemeral_card, validate_card


DEFAULTS = {"operational_status": 0, "hours_at_arrival": 0, "detour": 0, "layout": 548}


def draft() -> dict:
    return {
        "category": "pool",
        "languages": {"en": ["pool"], "it": ["piscina"]},
        "places_types": ["swimming_pool"],
        "query_templates": ["{name} orari"],
        "load_bearing_claims": ["operational_status", "hours_at_arrival", "detour", "layout"],
        "freshness_overrides": {"layout": 30},
        "event_shaped": False,
        "reviewed": False,
    }


def test_ephemeral_card_cannot_add_registry_sources() -> None:
    card = draft()
    card["sources"] = {"IT": {"random_blog": 1.0}}
    with pytest.raises(CardValidationError, match="sources"):
        validate_card(card, DEFAULTS)


def test_card_cannot_drop_core_claim_or_loosen_freshness() -> None:
    card = draft()
    card["load_bearing_claims"].remove("detour")
    with pytest.raises(CardValidationError, match="core claims"):
        validate_card(card, DEFAULTS)
    card = draft()
    card["freshness_overrides"]["layout"] = 549
    with pytest.raises(CardValidationError, match="tighten"):
        validate_card(card, DEFAULTS)


def test_ephemeral_save_and_reviewed_promotion(tmp_path: Path) -> None:
    cards = tmp_path / "cards"
    drafts = cards / "drafts"
    cards.mkdir()
    path = save_ephemeral_card(draft(), drafts, DEFAULTS)
    assert path.exists()
    promoted = promote_card("pool", cards, drafts, DEFAULTS)
    assert promoted.name == "pool_generic.yaml"
    assert yaml.safe_load(promoted.read_text())["reviewed"] is True


def test_source_weights_are_bounded_ranking_data() -> None:
    card = draft()
    card["reviewed"] = True
    card["sources"] = {"IT": {"guide": 1.1}}
    with pytest.raises(CardValidationError, match="0 to 1"):
        validate_card(card, DEFAULTS)
