import sqlite3
import time
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlparse

from osusume.adapters import RecordedAdapters, ReplayStore, SnapshotRecorder, WebAdapter, _OfficialPageParser
from osusume.config import load_config
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel
from osusume.room_index import RoomIndex, page_fingerprint
from tests.helpers import FakeModel, FakePlaces, operational_details, operational_place
from tests.test_hotels import FakeBooking, booking_row, hotel_request


NOW = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
ATTRIBUTE = {
    "claim_id": "suite_hot_tub",
    "claim_type": "layout",
    "text": "A private hot tub is in the suite",
    "synonyms": ["jacuzzi", "whirlpool"],
}


def page(text: str = "Penthouse: 90 m² terrace with jacuzzi") -> dict:
    return {
        "url": "https://hotel.example/rooms",
        "text": text,
        "source_kind": "official",
        "identity_label": "exact-venue",
        "room_passages": [{"room_name": "Penthouse", "text": text, "language": "en"}],
    }


def test_index_stores_pages_passages_fingerprint_freshness_and_failure_ttl(tmp_path) -> None:
    path = tmp_path / "rooms.sqlite"
    index = RoomIndex(path)
    index.replace("p1", [page()], now=NOW)

    fresh = index.lookup("p1", now=NOW + timedelta(days=29), max_age_days=30)
    assert fresh.status == "fresh"
    assert fresh.room_passages[0]["room_name"] == "Penthouse"
    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT fingerprint FROM official_pages WHERE place_id = ?", ("p1",)
        ).fetchone()[0]
    assert stored == page_fingerprint(page()["text"])
    assert index.lookup("p1", now=NOW + timedelta(days=31), max_age_days=30).status == "stale"

    index.record_failure("p1", now=NOW)
    assert index.lookup("p1", now=NOW + timedelta(hours=11), failure_ttl_hours=12).status == "failure"
    assert index.lookup("p1", now=NOW + timedelta(hours=13), failure_ttl_hours=12).status == "miss"


def test_index_replace_deduplicates_pages_with_the_same_url(tmp_path) -> None:
    path = tmp_path / "rooms.sqlite"
    index = RoomIndex(path)
    index.replace("p1", [page("Penthouse: first"), page("Penthouse: second")], now=NOW)

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT page_json FROM official_pages WHERE place_id = ?", ("p1",)
        ).fetchall()
    assert len(rows) == 1
    assert 'Penthouse: first' in rows[0][0]


class Places:
    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows

    def details(self, place_id: str, local_language: str) -> dict:
        return deepcopy(self.rows[place_id])


class Web:
    def __init__(self, official: dict[str, dict], *, delay: bool = False) -> None:
        self.official = official
        self.delay = delay
        self.official_calls = []
        self.official_completed = []
        self.mine_calls = []

    def official_pages(self, candidate: dict, details: dict, card: dict) -> dict:
        self.official_calls.append(candidate["place_id"])
        if self.delay:
            time.sleep(0.015 if candidate["place_id"] == "p1" else 0.001)
        self.official_completed.append(candidate["place_id"])
        return deepcopy(self.official[candidate["place_id"]])

    def mine(self, candidate: dict, request: dict, card: dict) -> dict:
        self.mine_calls.append(candidate["place_id"])
        return {"pages": [], "evidence": []}


def structured() -> StructuredRequest:
    return StructuredRequest.from_dict(
        {
            "ask": "hotel with a private hot tub in the suite",
            "category": "hotel",
            "local_language": "en",
            "required_attributes": [ATTRIBUTE],
            "scope": {"kind": "near", "city": "Test"},
        }
    )


def details(website: str | None = "https://hotel.example/") -> dict:
    result = operational_details()
    if website:
        result["en"]["websiteUri"] = website
        result["local"]["websiteUri"] = website
    return result


def engine(tmp_path, places: Places, web: Web, now: datetime, recorder=None) -> Funnel:
    config = load_config()
    return Funnel(
        config,
        RecordedAdapters(places, web, None, recorder=recorder),
        now=now,
        room_index=RoomIndex(tmp_path / "rooms.sqlite"),
    )


def test_funnel_reuses_fresh_index_refetches_stale_and_skips_web_mining(tmp_path) -> None:
    places = Places({"p1": details()})
    web = Web({"p1": {"pages": [page()], "evidence": [], "fetch_failed": False}})
    candidate = Candidate.from_place(operational_place("p1", "Hotel One", "hotel"))

    first = engine(tmp_path, places, web, NOW)
    mined = first.stage3_mine([candidate], structured(), {"sweep_source": "booking"}, "full")
    assert web.official_calls == ["p1"]
    assert web.mine_calls == []
    assert mined["p1"]["room_passages"][0]["room_name"] == "Penthouse"

    warm = engine(tmp_path, places, web, NOW + timedelta(days=1))
    warm.stage3_mine([candidate], structured(), {"sweep_source": "booking"}, "full")
    assert web.official_calls == ["p1"]
    assert warm.coverage == {"candidates": 0, "verified": 0, "pending": [], "from_index": 1, "fetched": 0}

    stale = engine(tmp_path, places, web, NOW + timedelta(days=31))
    web.official["p1"] = {"pages": [page("Royal Suite: private whirlpool")], "evidence": [], "fetch_failed": False}
    stale.stage3_mine([candidate], structured(), {"sweep_source": "booking"}, "full")
    assert web.official_calls == ["p1", "p1"]
    assert stale.coverage["fetched"] == 1
    refreshed = RoomIndex(tmp_path / "rooms.sqlite").lookup("p1", now=NOW + timedelta(days=31))
    assert [row["text"] for row in refreshed.room_passages] == ["Royal Suite: private whirlpool"]


def test_failed_fetch_is_not_retried_inside_ttl_and_no_passage_mines_once(tmp_path) -> None:
    places = Places({"p1": details()})
    web = Web({"p1": {"pages": [], "evidence": [], "fetch_failed": True}})
    candidate = Candidate.from_place(operational_place("p1", "Hotel One", "hotel"))

    engine(tmp_path, places, web, NOW).stage3_mine(
        [candidate], structured(), {"sweep_source": "booking"}, "full"
    )
    engine(tmp_path, places, web, NOW + timedelta(hours=11)).stage3_mine(
        [candidate], structured(), {"sweep_source": "booking"}, "full"
    )
    engine(tmp_path, places, web, NOW + timedelta(hours=13)).stage3_mine(
        [candidate], structured(), {"sweep_source": "booking"}, "full"
    )

    assert web.official_calls == ["p1", "p1"]
    assert web.mine_calls == ["p1", "p1", "p1"]


def test_no_passage_without_website_does_not_run_web_mining(tmp_path) -> None:
    places = Places({"p1": details(None)})
    web = Web({"p1": {"pages": [], "evidence": [], "fetch_failed": False}})
    candidate = Candidate.from_place(operational_place("p1", "Hotel One", "hotel"))

    current = engine(tmp_path, places, web, NOW)
    current.stage3_mine([candidate], structured(), {"sweep_source": "booking"}, "full")

    assert web.mine_calls == []
    assert current.coverage["fetched"] == 0


def test_concurrent_official_results_are_recorded_in_candidate_order(tmp_path) -> None:
    candidates = [
        Candidate.from_place(operational_place("p1", "Hotel One", "hotel")),
        Candidate.from_place(operational_place("p2", "Hotel Two", "hotel")),
    ]
    places = Places({"p1": details("https://one.example/"), "p2": details("https://two.example/")})
    web = Web(
        {
            "p1": {"pages": [page("Penthouse: jacuzzi")], "evidence": [], "fetch_failed": False},
            "p2": {
                "pages": [{**page("Royal Suite: whirlpool"), "url": "https://two.example/rooms"}],
                "evidence": [],
                "fetch_failed": False,
            },
        },
        delay=True,
    )
    recorder = SnapshotRecorder(tmp_path / "run")

    engine(tmp_path / "cache", places, web, NOW, recorder).stage3_mine(
        candidates, structured(), {"sweep_source": "booking"}, "full"
    )

    recorded = [
        row["request"]["candidate"]["place_id"]
        for row in recorder.calls
        if row["operation"] == "official_pages"
    ]
    assert web.official_completed == ["p2", "p1"]
    assert recorded == ["p1", "p2"]


def test_parser_extracts_heading_list_and_heading_paragraph_passages() -> None:
    parser = _OfficialPageParser()
    parser.feed(
        """<html lang="en"><body>
        <h2>Penthouse: Jacuzzi on the private terrace</h2>
        <ul><li>Garden Suite: whirlpool beside the bed</li></ul>
        <h2>Royal Suite</h2><p>A private balcony with jacuzzi</p>
        </body></html>"""
    )

    assert {row["text"] for row in parser.room_passages} >= {
        "Penthouse: Jacuzzi on the private terrace",
        "Garden Suite: whirlpool beside the bed",
        "A private balcony with jacuzzi",
    }
    assert next(row for row in parser.room_passages if row["text"] == "A private balcony with jacuzzi")["room_name"] == "Royal Suite"
    assert {row["language"] for row in parser.room_passages} == {"en"}


def test_room_lane_record_and_replay_match_with_exact_coverage_line(tmp_path) -> None:
    parsed = hotel_request([ATTRIBUTE])
    parsed["ask"] = "hotel with a private hot tub in the suite"
    place = operational_place("p1", "Hotel Uno", "hotel")
    current_details = operational_details()
    current_details["en"]["websiteUri"] = "https://hotel.example/"
    current_details["local"]["websiteUri"] = "https://hotel.example/"
    fixture = {"resolved": {"Hotel Uno": place}, "details": {"p1": current_details}}

    class FullWeb(Web):
        def registry(self, request: dict, card: dict, candidates=None) -> dict:
            return {"qualifications": [], "injected": []}

    web = FullWeb({"p1": {"pages": [page()], "evidence": [], "fetch_failed": False}})
    config = load_config()
    config["paths"]["drafts"] = tmp_path / "drafts"
    recorder = SnapshotRecorder(tmp_path / "run")
    raw_input = {"ask": parsed["ask"], "card": "hotel", "depth": "full", "deep_dive": True}
    recorded = Funnel(
        config,
        RecordedAdapters(
            FakePlaces(fixture), web, FakeModel(parsed), recorder=recorder, booking=FakeBooking([booking_row()])
        ),
        now=NOW,
        room_index=RoomIndex(tmp_path / "rooms.sqlite"),
    ).run(raw_input)
    recorder.finish(raw_input, recorded)

    replay = ReplayStore(tmp_path / "run")
    replayed = Funnel(
        config,
        RecordedAdapters(None, None, None, replay=replay),
        now=NOW,
    ).run(raw_input)

    assert replayed == recorded
    assert replay.index == len(replay.calls)
    assert recorded["coverage"]["from_index"] == 0
    assert recorded["coverage"]["fetched"] == 1
    assert recorded["human"].splitlines()[0] == "Checked 1 of 1 (0 from the index)"


def test_actual_social_host_requests_are_serialized(monkeypatch) -> None:
    guard = Lock()
    active: dict[str, int] = {}
    maximum: dict[str, int] = {}
    counts: dict[str, int] = {}

    class Headers(dict):
        def get_content_charset(self) -> str:
            return "utf-8"

    class Response:
        def __init__(self, url: str, body: str, host: str) -> None:
            self.url = url
            self.body = body.encode()
            self.host = host
            self.headers = Headers({"Content-Type": "text/html; charset=utf-8"})

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            with guard:
                active[self.host] -= 1

        def read(self, size: int = -1) -> bytes:
            time.sleep(0.01)
            return self.body[:size]

        def geturl(self) -> str:
            return self.url

    def fetch(request, timeout):
        url = request.full_url
        host = urlparse(url).hostname or ""
        with guard:
            active[host] = active.get(host, 0) + 1
            maximum[host] = max(maximum.get(host, 0), active[host])
            counts[host] = counts.get(host, 0) + 1
        body = (
            f'<html><body><a href="https://instagram.com/{host}">Instagram</a></body></html>'
            if host != "instagram.com"
            else "<html><body>Official social page</body></html>"
        )
        return Response(url, body, host)

    monkeypatch.setattr("osusume.adapters.urlopen", fetch)
    adapter = WebAdapter("https://unused.example")
    details_by_id = {
        "p1": {"en": {"websiteUri": "https://one.example/"}},
        "p2": {"en": {"websiteUri": "https://two.example/"}},
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                adapter.official_pages,
                {"place_id": place_id, "name": place_id},
                details,
                {"official_link_terms": []},
            )
            for place_id, details in details_by_id.items()
        ]
        [future.result() for future in futures]

    assert counts["instagram.com"] == 2
    assert maximum["instagram.com"] == 1
