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
    assert [{k: v for k, v in row.items() if k != "note"} for row in calls[0]] == passages
    assert calls[0][0]["note"] == "shared-context words present"


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


# Passages from the 2026-09-07 Barcelona cold run. The first three are real
# in-room tubs on the hotel's own pages; the rest are rooftop, spa or plain
# bathtubs that the proof used to accept.
MUST_ACCEPT = [
    {
        "room_name": "El auténtico lujo es sentirte",
        "text": (
            "Todas las habitaciones son exteriores, amplias y muy luminosas. Con camas premium, tecnología, "
            "bañera de hidromasaje, mobiliario de diseño y amenities personalizados."
        ),
    },
    {
        "room_name": "Master Suite",
        "text": "Master Suite con Jacuzzi en la Terraza. Disfruta de una terraza privada con jacuzzi y vistas al mar.",
    },
    {"room_name": "Jacuzzi Suite", "text": "Jacuzzi Suite con bañera de hidromasaje en la habitación"},
    {"room_name": "Suite Bañera Hidromasaje", "text": "Suite Bañera Hidromasaje: amplia suite con bañera de hidromasaje"},
]
MUST_NOT_ACCEPT = [
    {"room_name": "Jacuzzi exterior (de 10", "text": "Jacuzzi exterior (de 10:00 h a 21:00 h)"},
    {"room_name": "Habitaciones", "text": "Bañera con ducha"},
    {
        "room_name": "Terraza",
        "text": "una terraza con bañera de hidromasaje en la terraza de la última planta",
    },
    {"room_name": "Servicios", "text": "Disponemos en Jacuzzi en nuestra Terraza"},
    {"room_name": "Facilities", "text": "The hotel has an outdoor pool and a Jacuzzi"},
    {"room_name": "ROOFTOP TERRACE WITH POOL", "text": "rooftop terrace, pool and jacuzzi"},
    {"room_name": "Suites", "text": "Our suites enjoy access to the rooftop terrace, pool and jacuzzi"},
]
LIVE_ATTRIBUTE = {
    "text": "A private hot tub is in the room",
    "synonyms": ["jacuzzi", "whirlpool", "bañera de hidromasaje", "bañera", "bathtub"],
}


def test_own_site_room_passages_are_code_accepted_with_verbatim_quote() -> None:
    for passage in MUST_ACCEPT:
        result = evaluate_room_proof(LIVE_ATTRIBUTE, [passage])
        assert result.status == "proved", passage
        assert result.passage["text"] == passage["text"]


def test_rooftop_spa_and_bathtub_passages_are_never_code_accepted() -> None:
    for passage in MUST_NOT_ACCEPT:
        result = evaluate_room_proof(LIVE_ATTRIBUTE, [passage])
        assert result.status != "proved", passage
        assert result.status in {"judge", "unknown"}


def test_shared_context_passages_reach_the_judge_with_a_note() -> None:
    calls = []
    passages = [
        {"room_name": "Junior Suite", "text": "Junior Suite: rooftop pool and jacuzzi for all guests"},
        {"room_name": "Servicios", "text": "Jacuzzi exterior (de 10:00 h a 21:00 h)"},
    ]

    result = prove_room_attribute(LIVE_ATTRIBUTE, passages, lambda rows: calls.append(rows) or {"entails": False})

    assert result.status == "judged"
    assert [row["note"] for row in calls[0]] == ["shared-context words present"] * 2


def test_plain_bathtub_stays_unknown_without_judge() -> None:
    calls = []

    result = prove_room_attribute(
        LIVE_ATTRIBUTE,
        [{"room_name": "Habitaciones", "text": "Bañera con ducha"}],
        lambda rows: calls.append(rows),
    )

    assert result.status == "unknown"
    assert calls == []


def test_room_word_in_sentence_ties_a_heading_passage() -> None:
    tied = evaluate_room_proof(LIVE_ATTRIBUTE, [{"room_name": "", "text": "All rooms have a private jacuzzi."}])
    untied = evaluate_room_proof(LIVE_ATTRIBUTE, [{"room_name": "", "text": "A jacuzzi is available."}])

    assert tied.status == "proved"
    assert untied.status == "judge"
