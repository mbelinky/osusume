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


def test_source_domains_are_accepted_on_reviewed_cards() -> None:
    card = draft()
    card["reviewed"] = True
    card["source_domains"] = {"unknown_guide": ["guide.example"]}

    assert validate_card(card, DEFAULTS) == card


def test_ephemeral_card_cannot_add_source_domains() -> None:
    card = draft()
    card["source_domains"] = {"guide": ["guide.example"]}

    with pytest.raises(CardValidationError, match="source_domains"):
        validate_card(card, DEFAULTS)


def test_contact_questions_are_accepted_on_reviewed_cards() -> None:
    card = draft()
    card["reviewed"] = True
    card["contact_questions"] = {
        "layout": {
            "it": "Avete tavoli all'aperto?",
            "en": "Do you have outdoor tables?",
        }
    }

    assert validate_card(card, DEFAULTS) == card


def test_ephemeral_card_cannot_add_contact_questions() -> None:
    card = draft()
    card["contact_questions"] = {"layout": {"en": "Do you have outdoor tables?"}}

    with pytest.raises(CardValidationError, match="contact_questions"):
        validate_card(card, DEFAULTS)


def test_ephemeral_save_drops_contact_questions(tmp_path: Path) -> None:
    card = draft()
    card["contact_questions"] = {"layout": {"en": "Do you have outdoor tables?"}}

    path = save_ephemeral_card(card, tmp_path, DEFAULTS)

    assert "contact_questions" not in yaml.safe_load(path.read_text())


@pytest.mark.parametrize(
    "questions",
    ["Do you have outdoor tables?", {"en": ""}, {"en": "   "}],
)
def test_contact_questions_must_map_languages_to_non_empty_strings(questions) -> None:
    card = draft()
    card["reviewed"] = True
    card["contact_questions"] = {"layout": questions}

    with pytest.raises(CardValidationError, match="contact_questions"):
        validate_card(card, DEFAULTS)


@pytest.mark.parametrize("domains", ["guide.example", ["guide.example", 1], [""]])
def test_source_domains_must_be_lists_of_non_empty_strings(domains) -> None:
    card = draft()
    card["reviewed"] = True
    card["source_domains"] = {"guide": domains}

    with pytest.raises(CardValidationError, match="list of non-empty strings"):
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
