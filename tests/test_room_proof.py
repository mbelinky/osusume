from osusume.room_proof import evaluate_room_proof, prove_room_attribute


ATTRIBUTE = {
    "text": "A private hot tub is in the room",
    "synonyms": ["jacuzzi", "whirlpool", "bañera de hidromasaje"],
}


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
        {"room_name": "Terrace Suite", "text": "Terrace Suite: shared whirlpool"},
    ]

    result = prove_room_attribute(ATTRIBUTE, passages, lambda rows: calls.append(rows) or {"entails": False})

    assert result.status == "judged"
    assert calls == [passages]


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
