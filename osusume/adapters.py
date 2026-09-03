from __future__ import annotations

import json
import os
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen


class AdapterError(RuntimeError):
    pass


ANCHOR_SPEEDS_M_PER_MIN = {
    "walk": 80,
    "bicycle": 250,
    "drive": 600,
    "transit": 350,
}


def anchor_radius_m(scope: dict) -> int:
    mode = str(scope.get("mode", "walk"))
    if mode not in ANCHOR_SPEEDS_M_PER_MIN:
        raise ValueError(f"unsupported anchor travel mode: {mode}")
    return min(30000, int(float(scope.get("max_min", 10)) * ANCHOR_SPEEDS_M_PER_MIN[mode]))


def _json_subprocess(command: list[str], *, input_data: dict | None = None) -> Any:
    completed = subprocess.run(
        command,
        input=json.dumps(input_data) if input_data is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise AdapterError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"command returned invalid JSON: {' '.join(command)}") from exc


def _places_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("places", "results", "candidates"):
            if isinstance(payload.get(key), list):
                return payload[key]
        if isinstance(payload.get("waypoints"), list):
            rows = []
            for waypoint in payload["waypoints"]:
                if isinstance(waypoint, dict) and isinstance(waypoint.get("results"), list):
                    rows.extend(waypoint["results"])
            return rows
    return []


def _dedupe_places(rows: list[dict]) -> list[dict]:
    unique = []
    seen_place_ids = set()
    for row in rows:
        place_id = row.get("place_id")
        if place_id is not None:
            if place_id in seen_place_ids:
                continue
            seen_place_ids.add(place_id)
        unique.append(row)
    return unique


class GoplacesAdapter:
    """Exact subprocess adapter for the installed goplaces CLI."""

    def __init__(self, executable: str = "goplaces") -> None:
        self.executable = executable

    def _run(self, args: list[str]) -> Any:
        return _json_subprocess([self.executable, *args, "--json"])

    def sweep(self, request: dict, card: dict) -> dict[str, Any]:
        scope = request.get("scope", {})
        language_rows = card.get("languages", {})
        results: list[dict] = []
        raw: list[dict] = []
        type_attempt_count = 0
        type_success_count = 0
        if scope.get("kind") == "route":
            for language, terms in language_rows.items():
                for term in terms:
                    args = [
                        "route",
                        term,
                        "--from",
                        str(scope["from"]),
                        "--to",
                        str(scope["to"]),
                        "--mode",
                        "DRIVE",
                        "--radius-m",
                        str(int(scope.get("radius_km", 1) * 1000)),
                        "--language",
                        language,
                    ]
                    payload = self._run(args)
                    raw.append({"command": args, "response": payload})
                    results.extend(_places_list(payload))
        else:
            lat = scope.get("lat")
            lng = scope.get("lng")
            radius_m = anchor_radius_m(scope) if scope.get("kind") == "anchor" else int(scope.get("radius_km", 5) * 1000)
            for language, terms in language_rows.items():
                for term in terms:
                    args = ["search", term, "--language", language, "--lat", str(lat), "--lng", str(lng), "--radius-m", str(radius_m)]
                    payload = self._run(args)
                    raw.append({"command": args, "response": payload})
                    results.extend(_places_list(payload))
                for place_type in card.get("places_types", []):
                    args = ["nearby", "--type", place_type, "--language", language, "--lat", str(lat), "--lng", str(lng), "--radius-m", str(radius_m)]
                    type_attempt_count += 1
                    try:
                        payload = self._run(args)
                    except AdapterError as exc:
                        raw.append({"command": args, "error": str(exc)})
                        continue
                    type_success_count += 1
                    raw.append({"command": args, "response": payload})
                    results.extend(_places_list(payload))
        return {
            "candidates": _dedupe_places(results) if scope.get("kind") == "route" else results,
            "raw_calls": raw,
            "type_attempt_count": type_attempt_count,
            "type_success_count": type_success_count,
        }

    def resolve(self, name: str, request: dict) -> dict | None:
        scope = request.get("scope", {})
        args = ["search", name, "--limit", "1"]
        if scope.get("kind") in {"near", "anchor"} and scope.get("lat") is not None and scope.get("lng") is not None:
            radius_m = anchor_radius_m(scope) if scope.get("kind") == "anchor" else int(scope.get("radius_km", 5) * 1000)
            args.extend(["--lat", str(scope["lat"]), "--lng", str(scope["lng"]), "--radius-m", str(radius_m)])
        rows = _places_list(self._run(args))
        return rows[0] if rows else None

    def details(self, place_id: str, local_language: str) -> dict[str, Any]:
        en = self._run(["details", place_id, "--language", "en", "--reviews", "--photos"])
        local = self._run(["details", place_id, "--language", local_language, "--reviews", "--photos"])
        result = {"en": en, "local": local}
        api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
        if not api_key:
            return result
        # Temporary until goplaces exposes an --hours flag.
        url = (
            f"https://places.googleapis.com/v1/places/{quote(place_id, safe='')}"
            "?fields=regularOpeningHours,currentOpeningHours,utcOffsetMinutes"
        )
        request = Request(url, headers={"X-Goog-Api-Key": api_key}, method="GET")
        try:
            with urlopen(request, timeout=30) as response:
                supplement = json.loads(response.read())
        except Exception:
            return result
        if isinstance(supplement, dict):
            result["hours_supplement"] = supplement
        return result

    def directions(
        self,
        start: str,
        end: str,
        departure_time: str | None = None,
        *,
        start_is_place_id: bool = False,
        end_is_place_id: bool = False,
        mode: str = "drive",
    ) -> dict:
        start_flag = "--from-place-id" if start_is_place_id else "--from"
        end_flag = "--to-place-id" if end_is_place_id else "--to"
        args = ["directions", start_flag, start, end_flag, end, "--mode", mode]
        if departure_time:
            args.extend(["--departure-time", departure_time])
        return self._run(args)

    def photo(self, photo_name: str) -> dict:
        return self._run(["photo", photo_name, "--max-width", "1600"])


class WebAdapter:
    def __init__(self, endpoint: str, api_key: str | None = None) -> None:
        self.endpoint = endpoint
        self.api_key = api_key or os.environ.get("EXA_API_KEY")

    def _search(self, query: str) -> dict:
        if not self.api_key:
            raise AdapterError("EXA_API_KEY is required for a live run")
        body = json.dumps({"query": query, "numResults": 5, "contents": {"text": True}}).encode()
        request = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json", "x-api-key": self.api_key},
            method="POST",
        )
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    def registry(self, request: dict, card: dict, candidates: list[dict] | None = None) -> dict[str, Any]:
        country = request.get("country", "IT")
        rows = []
        qualifications = []
        injected = []
        for source in (card.get("sources", {}).get(country, {}) or {}):
            query = f"{source.replace('_', ' ')} {request.get('category', '')} {request.get('ask', '')}"
            response = self._search(query)
            rows.append({"source": source, "response": response})
            for result in response.get("results", []):
                text = f"{result.get('title', '')} {result.get('text', '')}".lower()
                roundup = any(term in text for term in ("best ", "top ", "migliori", "roundup"))
                explicit_rated = result.get("entry_type") == "rated_entry" and bool(result.get("rating") or result.get("score"))
                entry_type = "rated_entry" if explicit_rated and not roundup else "mention"
                for candidate in candidates or []:
                    if candidate.get("name", "").casefold() in text.casefold():
                        qualifications.append(
                            {
                                "place_id": candidate["place_id"],
                                "source": source,
                                "entry_type": entry_type,
                                "url": result.get("url", ""),
                                "title": result.get("title", ""),
                                "text": result.get("text", ""),
                                "date": result.get("publishedDate"),
                            }
                        )
                if result.get("entity_name"):
                    injected.append(
                        {
                            "name": result["entity_name"],
                            "source": source,
                            "entry_type": entry_type,
                            "url": result.get("url", ""),
                        }
                    )
        return {"sources": rows, "qualifications": qualifications, "injected": injected}

    def mine(self, candidate: dict, request: dict, card: dict) -> dict[str, Any]:
        queries: list[tuple[str, str]] = []
        templates = card.get("query_templates", [])
        city = request.get("scope", {}).get("city", "")
        for template in templates:
            if isinstance(template, dict):
                template_text = template.get("template") or template.get("query") or ""
                claim_id = template.get("claim_id") or template.get("claim_type") or "quality"
            else:
                template_text = template
                claim_id = "quality"
            queries.append((template_text.format(name=candidate["name"], city=city, category=request.get("category", "")), claim_id))
        for attribute in request.get("required_attributes", []):
            attribute_text = attribute.get("text") or attribute.get("claim_id") or attribute.get("claim_type", "")
            claim_id = attribute.get("claim_id") or re.sub(r"[^a-z0-9]+", "_", attribute_text.lower()).strip("_") or "claim"
            queries.append((f"{candidate['name']} {attribute_text}", claim_id))
        pages = []
        for query, claim_id in queries:
            result = self._search(query)
            for row in result.get("results", []):
                pages.append(
                    {
                        "query": query,
                        "claim_id": claim_id,
                        "url": row.get("url", ""),
                        "title": row.get("title", ""),
                        "text": row.get("text", ""),
                        "published_date": row.get("publishedDate"),
                        "retrieved_at": result.get("retrieved_at"),
                        "source_kind": row.get("source_kind", "generic_web"),
                    }
                )
        return {"pages": pages}


class ModelAdapter:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def run(self, slot: str, payload: dict[str, Any]) -> dict[str, Any]:
        models = self.config["models"]
        model = models.get("task_overrides", {}).get(slot, models["default_model"])
        command_template = models.get("commands", {}).get(slot) or models.get("commands", {}).get("default")
        if not command_template:
            raise AdapterError(f"no model command configured for {slot}")
        command = [part.format(model=model) for part in command_template]
        try:
            response = _json_subprocess(command, input_data={"slot": slot, "payload": payload})
        except AdapterError:
            fallback = models.get("fallback_model")
            if not fallback or fallback == model:
                raise
            command = [part.format(model=fallback) for part in command_template]
            response = _json_subprocess(command, input_data={"slot": slot, "payload": payload})
        if not isinstance(response, dict):
            raise AdapterError(f"model lane {slot} must return a JSON object")
        return response


class SnapshotRecorder:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_at = datetime.now(timezone.utc)
        self.raw_dir = run_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.calls: list[dict[str, Any]] = []

    def wrap(self, adapter_name: str, operation: str, request: dict, call: Callable[[], Any]) -> Any:
        try:
            response = call()
        except AdapterError as exc:
            record = {"adapter": adapter_name, "operation": operation, "request": request, "error": str(exc)}
            self._save_call(record)
            raise
        record = {"adapter": adapter_name, "operation": operation, "request": request, "response": response}
        self._save_call(record)
        return response

    def _save_call(self, record: dict[str, Any]) -> None:
        self.calls.append(record)
        path = self.raw_dir / f"{len(self.calls):04d}_{record['adapter']}_{record['operation']}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    def finish(self, input_data: dict, output: dict) -> None:
        payload = {"format_version": 1, "run_at": self.run_at.isoformat(), "input": input_data, "calls": self.calls, "output": output}
        (self.run_dir / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


class ReplayStore:
    def __init__(self, run_dir: Path) -> None:
        path = run_dir / "run.json"
        if not path.exists():
            raise FileNotFoundError(f"replay snapshot has no run.json: {run_dir}")
        self.payload = json.loads(path.read_text(encoding="utf-8"))
        self.calls = list(self.payload.get("calls", []))
        self.index = 0

    @property
    def input(self) -> dict:
        return deepcopy(self.payload.get("input", {}))

    @property
    def run_at(self) -> datetime:
        raw = self.payload.get("run_at")
        if not raw:
            raise AdapterError("replay snapshot has no run_at timestamp")
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    def take(self, adapter: str, operation: str, request: dict) -> Any:
        if self.index >= len(self.calls):
            raise AdapterError(f"replay exhausted before {adapter}.{operation}")
        record = self.calls[self.index]
        self.index += 1
        actual = (record.get("adapter"), record.get("operation"))
        expected = (adapter, operation)
        if actual != expected:
            raise AdapterError(f"replay call {self.index} expected {actual[0]}.{actual[1]}, got {adapter}.{operation}")
        recorded_request = record.get("request", {})
        if recorded_request and recorded_request != request:
            raise AdapterError(f"replay request mismatch for {adapter}.{operation}")
        if "error" in record:
            raise AdapterError(str(record["error"]))
        return deepcopy(record.get("response"))


class RecordedAdapters:
    def __init__(self, places: Any, web: Any, model: Any, recorder: SnapshotRecorder | None = None, replay: ReplayStore | None = None) -> None:
        self.places = places
        self.web = web
        self.model = model
        self.recorder = recorder
        self.replay = replay

    def call(self, adapter: str, operation: str, request: dict, fn: Callable[[], Any]) -> Any:
        if self.replay:
            return self.replay.take(adapter, operation, request)
        if self.recorder:
            return self.recorder.wrap(adapter, operation, request, fn)
        return fn()
