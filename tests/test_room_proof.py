import pytest

from osusume.room_proof import evaluate_room_proof, prove_room_attribute


ATTRIBUTE = {
    "text": "A private hot tub is in the room",
    "synonyms": ["jacuzzi", "whirlpool", "bañera de hidromasaje", "bañera", "bathtub", "baño"],
}

WRONG_PASSAGES = (
    (
        "barcelona-center-shared-outdoor",
        {"room_name": "Jacuzzi exterior (de 10", "text": "Jacuzzi exterior (de 10:00 h a 21:00 h)"},
        "judge",
    ),
    (
        "barcelona-center-bathtub",
        {"room_name": "Habitaciones", "text": "Bañera con ducha"},
        "unknown",
    ),
    (
        "ciutat-vella-top-floor",
        {
            "room_name": "Boutique Hotel de Barcelona en zona Ramblas",
            "text": (
                "El Hotel Ciutat Vella ofrece habitaciones modernas con todas las comodidades, Wi-Fi gratuita y "
                "una terraza con bañera de hidromasaje en la terraza de la última planta."
            ),
        },
        "judge",
    ),
    (
        "ciutat-vella-our-terrace",
        {"room_name": "Jacuzzi en terraza", "text": "Disponemos en Jacuzzi en nuestra Terraza"},
        "judge",
    ),
    (
        "petit-palace-eixample",
        {
            "room_name": "OUTDOOR POOL WITH SOLARIUM TERRACE AND JACUZZI",
            "text": "outdoor pool and a Jacuzzi",
        },
        "judge",
    ),
    (
        "petit-palace-aston",
        {
            "room_name": "ROOFTOP TERRACE WITH POOL AND JACUZZI",
            "text": "rooftop terrace, pool and jacuzzi",
        },
        "judge",
    ),
    (
        "petit-palace-junior",
        {
            "room_name": "ROOFTOP TERRACE WITH POOL AND JACUZZI",
            "text": "rooftop terrace, pool and jacuzzi",
        },
        "judge",
    ),
)

RIGHT_PASSAGES = (
    (
        "abac-all-rooms",
        {
            "room_name": "El auténtico lujo es sentirte como en casa",
            "text": "Todas las habitaciones tienen bañera de hidromasaje.",
        },
    ),
    (
        "abac-penthouse",
        {"room_name": "Penthouse", "text": "terraza con jacuzzi"},
    ),
    (
        "barcelona-princess-master-suite",
        {
            "room_name": "Master Suite con Jacuzzi en la Terraza",
            "text": (
                "Esta elegante suite dispone de una terraza privada con jacuzzi, perfecta para relajarte mientras "
                "disfrutas de vistas exclusivas a la ciudad y al mar."
            ),
        },
    ),
    (
        "sb-diagonal-zero-jacuzzi-suite",
        {"room_name": "Jacuzzi Suite", "text": "Jacuzzi Suite"},
    ),
    (
        "sb-diagonal-zero-hydromassage-suite",
        {"room_name": "Cada habitación tiene su personalidad", "text": "Suite Bañera Hidromasaje"},
    ),
)


def test_named_room_passage_proves_verbatim_without_judge() -> None:
    calls = []
    passage = {
        "room_name": "Penthouse",
        "text": "Penthouse: 90 m² terrace with jacuzzi",
        "page_url": "https://hotel.example/rooms",
    }

    result = prove_room_attribute(ATTRIBUTE, [passage], lambda rows: calls.append(rows))

    assert result.status == "proved"
    assert result.passage["text"] == passage["text"]
    assert calls == []


def test_shared_spa_passages_go_to_one_batched_judge_call() -> None:
    calls = []
    passages = [
        {"room_name": "Wellness", "text": "spa with jacuzzi (shared)"},
        {"room_name": "Terrace Suite", "text": "Terrace Suite: shared rooftop whirlpool"},
    ]

    result = prove_room_attribute(ATTRIBUTE, passages, lambda rows: calls.append(rows) or {"entails": False})

    assert result.status == "judged"
    assert len(calls) == 1
    assert [row["text"] for row in calls[0]] == [row["text"] for row in passages]
    assert all(row["room_proof_note"] == "shared-context words present" for row in calls[0])


def test_no_attribute_mention_stays_unknown_without_judge() -> None:
    calls = []

    result = prove_room_attribute(
        ATTRIBUTE,
        [{"room_name": "Penthouse", "text": "Penthouse: a 90 m² private terrace"}],
        lambda rows: calls.append(rows),
    )

    assert result.status == "unknown"
    assert calls == []
    assert evaluate_room_proof(ATTRIBUTE, []).status == "unknown"


def test_standalone_negation_and_generic_heading_cannot_auto_prove() -> None:
    negated = evaluate_room_proof(
        ATTRIBUTE,
        [{"room_name": "Penthouse", "text": "Penthouse: no hot tub"}],
    )
    generic = evaluate_room_proof(
        ATTRIBUTE,
        [{"room_name": "Rooftop Spa", "text": "Jacuzzi"}],
    )

    assert negated.status == "judge"
    assert generic.status == "judge"


def test_room_word_and_attribute_in_different_sentences_cannot_auto_prove() -> None:
    result = evaluate_room_proof(
        ATTRIBUTE,
        [{"room_name": "Amenities", "text": "Our rooms are bright. A jacuzzi is available."}],
    )

    assert result.status == "judge"
    assert not result.proved


@pytest.mark.parametrize(("_venue", "passage", "expected_status"), WRONG_PASSAGES, ids=lambda value: value if isinstance(value, str) else None)
def test_live_false_positive_passages_are_not_code_accepted(
    _venue: str,
    passage: dict,
    expected_status: str,
) -> None:
    result = evaluate_room_proof(ATTRIBUTE, [passage])

    assert result.status == expected_status
    assert not result.proved
    if expected_status == "judge":
        assert result.candidate_passages[0]["room_proof_note"] == "shared-context words present"
    else:
        assert result.candidate_passages == ()


@pytest.mark.parametrize(("_venue", "passage"), RIGHT_PASSAGES, ids=lambda value: value if isinstance(value, str) else None)
def test_live_room_passages_are_code_accepted_with_verbatim_quote(_venue: str, passage: dict) -> None:
    result = evaluate_room_proof(ATTRIBUTE, [passage])

    assert result.status == "proved"
    assert result.passage is not None
    assert result.passage["text"] == passage["text"]


def test_private_context_in_another_sentence_does_not_override_shared_context() -> None:
    passage = {
        "room_name": "Penthouse",
        "text": "The rooftop pool has a jacuzzi. This room also has a private terrace.",
    }

    result = evaluate_room_proof(ATTRIBUTE, [passage])

    assert result.status == "judge"
    assert result.candidate_passages[0]["room_proof_note"] == "shared-context words present"


def test_private_context_with_attribute_in_same_sentence_overrides_shared_heading() -> None:
    passage = {
        "room_name": "Rooftop Suite",
        "text": "This suite has an in-room jacuzzi.",
    }

    result = evaluate_room_proof(ATTRIBUTE, [passage])

    assert result.status == "proved"
    assert result.passage is not None
    assert result.passage["text"] == passage["text"]


def test_room_word_in_the_attribute_sentence_ties_a_heading_passage() -> None:
    tied = evaluate_room_proof(ATTRIBUTE, [{"room_name": "", "text": "All rooms have a private jacuzzi."}])
    untied = evaluate_room_proof(ATTRIBUTE, [{"room_name": "", "text": "A jacuzzi is available."}])

    assert tied.status == "proved"
    assert untied.status == "judge"
