import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

from osusume.adapters import AdapterError, ReplayStore, SnapshotRecorder
from osusume.cli import main


def replay(name: str) -> dict:
    output = StringIO()
    with redirect_stdout(output), pytest.raises(SystemExit) as exit_info:
        main(
            [
                "find",
                "--replay",
                f"tests/fixtures/runs/{name}",
                "--json",
            ]
        )
    assert exit_info.value.code == 0
    return json.loads(output.getvalue())


def test_e_f3_replay_refuses_dead_shop() -> None:
    output = replay("e_f3")
    assert output["candidates"][0]["place_id"] == "dead-shop"
    assert output["candidates"][0]["verdict"] == "rejected"
    assert output["candidates"][0]["reason"] == "dead_at_sweep"
    assert output["refusal"] is True


def test_e_f6_replay_is_near_miss_without_counter_claim() -> None:
    output = replay("e_f6")
    candidate = output["candidates"][0]
    assert candidate["verdict"] == "near_miss"
    counter = next(row for row in candidate["claims"] if row["claim_id"] == "counter_service")
    assert counter["status"] == "wrong_source"
    assert "confirmed" not in " ".join(candidate["rendered_claims"]).lower()


def test_snapshot_stores_each_raw_response_and_replays_it(tmp_path: Path) -> None:
    recorder = SnapshotRecorder(tmp_path)
    response = recorder.wrap("web", "mine", {"query": "x"}, lambda: {"pages": [{"text": "raw"}]})
    recorder.finish({"ask": "x"}, {"ok": True})
    assert response["pages"][0]["text"] == "raw"
    assert len(list((tmp_path / "raw").glob("*.json"))) == 1
    replay = ReplayStore(tmp_path)
    assert replay.run_at == recorder.run_at
    assert replay.take("web", "mine", {"query": "x"}) == response


def test_snapshot_stores_adapter_error_and_replays_it(tmp_path: Path) -> None:
    recorder = SnapshotRecorder(tmp_path)

    def fail() -> None:
        raise AdapterError("no directions returned")

    with pytest.raises(AdapterError, match="no directions returned"):
        recorder.wrap("goplaces", "directions", {"start": "A", "end": "p1"}, fail)
    recorder.finish({"ask": "x"}, {"ok": True})

    replay = ReplayStore(tmp_path)
    with pytest.raises(AdapterError, match="no directions returned"):
        replay.take("goplaces", "directions", {"start": "A", "end": "p1"})
