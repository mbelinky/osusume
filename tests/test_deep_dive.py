import json

import pytest

from osusume.adapters import AdapterError
from osusume.cli import _parser, _raw_input, _run_find
from osusume.config import load_config
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place, request


class CountingModel(FakeModel):
    def __init__(self, fail_at=None, message="You've hit your session limit", fail_assemble=False):
        super().__init__(request())
        self.judged = []
        self.fail_at = fail_at
        self.message = message
        self.fail_assemble = fail_assemble
        self.slots = []

    def run(self, slot, payload):
        self.slots.append(slot)
        if slot == "judge":
            self.judged.append(payload["place_id"])
            if len(self.judged) == self.fail_at:
                raise AdapterError(self.message)
        if slot == "assemble" and self.fail_assemble:
            raise AdapterError(self.message)
        return super().run(slot, payload)


def setup_run(monkeypatch, tmp_path, model, count=7):
    fixture = {
        "sweep": [operational_place(f"p{i}") for i in range(count)],
        "details": {f"p{i}": operational_details(f"p{i}") for i in range(count)},
    }
    monkeypatch.setattr("osusume.cli.GoplacesAdapter", lambda: FakePlaces(fixture))
    monkeypatch.setattr("osusume.cli.WebAdapter", lambda *a, **kw: FakeWeb(fixture))
    monkeypatch.setattr("osusume.cli.ModelAdapter", lambda config: model)
    config = load_config()
    config["retrieval"]["deep_dive_batch"] = 2
    config["paths"]["runs"] = tmp_path
    return config


def find_args(tmp_path, *extra):
    return _parser().parse_args([
        "find", "test", "--near", "42,12", "--card", "salumeria",
        "--run-dir", str(tmp_path), "--json", *extra,
    ])


@pytest.mark.parametrize("flags,expected", [([], 5), (["--top", "2"], 2), (["--deep-dive", "--depth", "quick"], 7), (["--top", "6", "--depth", "quick"], 6)])
def test_caps_and_batches(monkeypatch, tmp_path, capsys, flags, expected):
    model = CountingModel()
    config = setup_run(monkeypatch, tmp_path, model)
    assert _run_find(find_args(tmp_path, *flags), config) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["coverage"] == {"candidates": 7, "verified": expected, "pending": []}
    assert packet["partial"] is False
    assert len(model.judged) == expected
    assert f"Checked {expected} of 7 candidates" in packet["human"]
    if expected < 7:
        assert f"(top {expected}); use --deep-dive for all" in packet["human"]
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert len(checkpoint["done"]) == expected
    assert checkpoint["pending"] == []
    assert all(row["ledger"]["frozen"] for row in checkpoint["candidates"])
    snapshot = json.loads((tmp_path / "run.json").read_text())
    calls = snapshot["calls"]
    # Retrieval for the next batch starts only after judging the preceding one.
    judge_positions = [i for i, row in enumerate(calls) if row["operation"] == "judge"]
    detail_positions = [i for i, row in enumerate(calls) if row["operation"] == "details"]
    if expected > 2:
        assert judge_positions[1] < detail_positions[2]
    if "--deep-dive" in flags:
        assert snapshot["input"]["depth"] == "full"
        assert snapshot["input"]["deep_dive"] is True
        assert packet["request"]["deep_dive"] is True


@pytest.mark.parametrize("fail_at,message", [(5, "You've hit your session limit"), (6, "model unavailable"), (1, "rate limit")])
def test_pause_resume_replays_finished_batches(monkeypatch, tmp_path, capsys, fail_at, message):
    model = CountingModel(fail_at, message)
    config = setup_run(monkeypatch, tmp_path, model)
    assert _run_find(find_args(tmp_path, "--deep-dive"), config) == 0
    captured = capsys.readouterr()
    packet = json.loads(captured.out)
    done = ((fail_at - 1) // 2) * 2
    assert packet["partial"] is True
    assert packet["coverage"] == {"candidates": 7, "verified": done, "pending": [f"p{i}" for i in range(done, 7)]}
    assert f"Deep dive paused after {done} of 7; resume with --resume {tmp_path}" in captured.err
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["done"] == [f"p{i}" for i in range(done)]
    assert checkpoint["pending"] == packet["coverage"]["pending"]
    original_raw = {path.name: path.read_bytes() for path in (tmp_path / "raw").glob("*.json")}

    working = CountingModel()
    monkeypatch.setattr("osusume.cli.ModelAdapter", lambda config: working)
    config["retrieval"]["deep_dive_batch"] = 5  # Resume retains the original batching.
    args = _parser().parse_args(["find", "--resume", str(tmp_path), "--json"])
    assert _run_find(args, config) == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["partial"] is False
    assert packet["coverage"] == {"candidates": 7, "verified": 7, "pending": []}
    assert working.judged == [f"p{i}" for i in range(done, 7)]
    assert "parse" not in working.slots
    assert all((tmp_path / "raw" / name).read_bytes() == content for name, content in original_raw.items())
    # The appended snapshot also remains usable by strict offline replay.
    assert _run_find(_parser().parse_args(["find", "--replay", str(tmp_path), "--json"]), config) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed == packet


def test_assembly_failure_preserves_completed_work(monkeypatch, tmp_path, capsys):
    model = CountingModel(fail_assemble=True)
    config = setup_run(monkeypatch, tmp_path, model)
    _run_find(find_args(tmp_path, "--deep-dive"), config)
    packet = json.loads(capsys.readouterr().out)
    assert packet["partial"] is True
    assert packet["coverage"] == {"candidates": 7, "verified": 7, "pending": []}
    working = CountingModel()
    monkeypatch.setattr("osusume.cli.ModelAdapter", lambda config: working)
    _run_find(_parser().parse_args(["find", "--resume", str(tmp_path), "--json"]), config)
    assert json.loads(capsys.readouterr().out)["partial"] is False
    assert working.slots == ["assemble"]


def test_unrelated_first_batch_failure_is_not_swallowed(monkeypatch, tmp_path):
    config = setup_run(monkeypatch, tmp_path, CountingModel(1, "model unavailable"))
    with pytest.raises(AdapterError, match="model unavailable"):
        _run_find(find_args(tmp_path, "--deep-dive"), config)


def test_positive_top_and_raw_input():
    with pytest.raises(SystemExit):
        _parser().parse_args(["find", "--top", "0"])
    raw = _raw_input(_parser().parse_args(["find", "--top", "2", "--deep-dive", "--depth", "quick"]))
    assert (raw["top"], raw["deep_dive"], raw["depth"]) == (2, True, "full")


def test_repeated_resume_and_cli_exit(monkeypatch, tmp_path, capsys):
    from osusume.cli import main

    config = setup_run(monkeypatch, tmp_path, CountingModel(5))
    monkeypatch.setattr("osusume.cli.load_config", lambda: config)
    with pytest.raises(SystemExit) as stopped:
        main(["find", "test", "--near", "42,12", "--card", "salumeria", "--deep-dive", "--run-dir", str(tmp_path), "--json"])
    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out)["coverage"]["verified"] == 4
    second = CountingModel(3)
    monkeypatch.setattr("osusume.cli.ModelAdapter", lambda config: second)
    with pytest.raises(SystemExit) as stopped:
        main(["find", "--resume", str(tmp_path), "--json"])
    assert stopped.value.code == 0
    assert json.loads(capsys.readouterr().out)["coverage"]["verified"] == 6
    assert second.judged == ["p4", "p5", "p6"]
    final = CountingModel()
    monkeypatch.setattr("osusume.cli.ModelAdapter", lambda config: final)
    with pytest.raises(SystemExit) as stopped:
        main(["find", "--resume", str(tmp_path), "--json"])
    assert stopped.value.code == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["partial"] is False
    assert final.judged == ["p6"]
    config["retrieval"]["deep_dive_batch"] = 5
    _run_find(_parser().parse_args(["find", "--replay", str(tmp_path), "--json"]), config)
    assert json.loads(capsys.readouterr().out) == packet


def test_parse_limit_can_resume(monkeypatch, tmp_path, capsys):
    class LimitedParse(CountingModel):
        def run(self, slot, payload):
            raise AdapterError("session limit")

    config = setup_run(monkeypatch, tmp_path, LimitedParse())
    _run_find(find_args(tmp_path, "--deep-dive"), config)
    assert json.loads(capsys.readouterr().out)["partial"] is True
    working = CountingModel()
    monkeypatch.setattr("osusume.cli.ModelAdapter", lambda config: working)
    _run_find(_parser().parse_args(["find", "--resume", str(tmp_path), "--json"]), config)
    assert json.loads(capsys.readouterr().out)["coverage"]["verified"] == 7
    assert len(working.judged) == 7


def test_rejected_candidates_count_as_checked(monkeypatch, tmp_path, capsys):
    config = setup_run(monkeypatch, tmp_path, CountingModel())
    places = FakePlaces({
        "sweep": [operational_place(f"p{i}") for i in range(7)],
        "details": {f"p{i}": operational_details(f"p{i}", "CLOSED_PERMANENTLY" if i == 0 else "OPERATIONAL") for i in range(7)},
    })
    monkeypatch.setattr("osusume.cli.GoplacesAdapter", lambda: places)
    _run_find(find_args(tmp_path, "--deep-dive"), config)
    packet = json.loads(capsys.readouterr().out)
    assert packet["coverage"] == {"candidates": 7, "verified": 7, "pending": []}
    assert next(row for row in packet["candidates"] if row["place_id"] == "p0")["verdict"] == "rejected"
