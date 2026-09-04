from __future__ import annotations

import math
import re
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from .adapters import AdapterError, RecordedAdapters, _candidate_details, _identity_label, _is_aggregator_domain, anchor_radius_m
from .cards import find_card, save_ephemeral_card, validate_card
from .domain import Candidate, StructuredRequest, utc_now
from .evidence import Claim, ClaimLedger, ClaimStatus, EvidenceRecord, registrable_domain


OPERATIONAL = "OPERATIONAL"

HOURS_CONTACT_QUESTIONS = {
    "it": "Sarete aperti durante il nostro orario di arrivo?",
    "es": "¿Estarán abiertos durante nuestro horario de llegada?",
    "ca": "Estareu oberts durant el nostre horari d'arribada?",
    "en": "Will you be open during our arrival window?",
    "fr": "Serez-vous ouverts pendant notre créneau d’arrivée ?",
    "pt": "Estarão abertos durante o nosso horário de chegada?",
    "de": "Werden Sie während unseres Ankunftszeitfensters geöffnet sein?",
}


class HoursWindowStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


ALLOWED_CLAIM_LABELS = {
    "operational_status",
    "hours_at_arrival",
    "detour",
    "proximity",
    "product_inventory",
    "counter_service",
    "layout",
    "quality",
    "importance",
    "event_schedule",
    "venue_type",
    "rating_signal",
}


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return value or "claim"


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _excluded(candidate: Candidate, exclusions: tuple[str, ...]) -> bool:
    names = {_norm(candidate.name), _norm(candidate.place_id)}
    return any(_norm(item) in names or (_norm(item) and _norm(item) in _norm(candidate.name)) for item in exclusions)


def _detail_body(payload: dict) -> dict:
    for key in ("place", "result"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    return payload


def _location_tuple(body: dict) -> tuple[float, float] | None:
    location = body.get("location") or {}
    lat = location.get("latitude", location.get("lat"))
    lng = location.get("longitude", location.get("lng"))
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _same_identity(place_id: str, left: dict, right: dict, tolerance: float = 0.001) -> bool:
    left_id = str(left.get("id") or left.get("place_id") or left.get("name", "")).removeprefix("places/")
    right_id = str(right.get("id") or right.get("place_id") or right.get("name", "")).removeprefix("places/")
    if left_id != place_id or right_id != place_id:
        return False
    a = _location_tuple(left)
    b = _location_tuple(right)
    if a is None or b is None:
        return True
    return math.dist(a, b) <= tolerance


def _haversine_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_lat, left_lng = map(math.radians, left)
    right_lat, right_lng = map(math.radians, right)
    delta_lat = right_lat - left_lat
    delta_lng = right_lng - left_lng
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(left_lat) * math.cos(right_lat) * math.sin(delta_lng / 2) ** 2
    return 2 * 6371.0088 * math.asin(min(1.0, math.sqrt(haversine)))


def _out_of_scope(candidate: Candidate, scope: dict[str, Any]) -> bool:
    if scope.get("kind") not in {"near", "anchor"}:
        return False
    center_lat = scope.get("lat")
    center_lng = scope.get("lng")
    radius_km = anchor_radius_m(scope) / 1000 if scope.get("kind") == "anchor" else scope.get("radius_km")
    location = _location_tuple({"location": candidate.location})
    if center_lat is None or center_lng is None or radius_km is None or location is None:
        return False
    return _haversine_km((float(center_lat), float(center_lng)), location) > float(radius_km)


def _parse_iso(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _period_bounds(period: dict, arrival: datetime) -> list[tuple[datetime, datetime]] | None:
    opened = period.get("open", {})
    closed = period.get("close", {})
    if not opened or not closed:
        return None
    try:
        open_day = int(opened.get("day", -1))
        close_day = int(closed.get("day", -1))
        open_time = time(int(opened.get("hour", 0)), int(opened.get("minute", 0)))
        close_time = time(int(closed.get("hour", 0)), int(closed.get("minute", 0)))
    except (TypeError, ValueError):
        return None
    if not 0 <= open_day <= 6 or not 0 <= close_day <= 6:
        return None
    google_day = (arrival.weekday() + 1) % 7
    week_start = arrival.date() - timedelta(days=google_day)
    start = datetime.combine(
        week_start + timedelta(days=open_day),
        open_time,
        arrival.tzinfo,
    )
    days_until_close = (close_day - open_day) % 7
    end = datetime.combine(
        week_start + timedelta(days=open_day + days_until_close),
        close_time,
        arrival.tzinfo,
    )
    if end <= start:
        end += timedelta(days=1)
    return [(start + timedelta(days=shift), end + timedelta(days=shift)) for shift in (-7, 0, 7)]


def _opening_hours(details: dict) -> tuple[dict | None, int | None]:
    sources = []
    supplement = details.get("hours_supplement")
    if isinstance(supplement, dict):
        sources.append(supplement)
    sources.append(details)
    fallback = None
    for source in sources:
        for key in ("regularOpeningHours", "currentOpeningHours", "opening_hours"):
            hours = source.get(key)
            if isinstance(hours, dict):
                offset = source.get("utcOffsetMinutes", details.get("utcOffsetMinutes"))
                candidate = (hours, int(offset) if offset is not None else None)
                if isinstance(hours.get("periods"), list):
                    return candidate
                fallback = fallback or candidate
    return fallback or (None, None)


def open_for_window(details: dict, start_raw: str, end_raw: str | None) -> HoursWindowStatus:
    start = _parse_iso(start_raw)
    end = _parse_iso(end_raw) if end_raw else start
    hours, utc_offset_minutes = _opening_hours(details)
    if hours is None or not isinstance(hours.get("periods"), list):
        return HoursWindowStatus.UNKNOWN
    if utc_offset_minutes is not None:
        place_timezone = timezone(timedelta(minutes=utc_offset_minutes))
        start = start.astimezone(place_timezone)
        end = end.astimezone(place_timezone)
    else:
        end = end.astimezone(start.tzinfo)
    invalid_period = False
    for period in hours["periods"]:
        bounds = _period_bounds(period, start)
        if bounds is None:
            invalid_period = True
            continue
        for period_start, period_end in bounds:
            if period_start <= start and end <= period_end:
                return HoursWindowStatus.OPEN
    return HoursWindowStatus.UNKNOWN if invalid_period else HoursWindowStatus.CLOSED


def _duration_minutes(payload: dict) -> float:
    for key in ("duration_seconds", "durationSeconds", "traffic_duration_seconds", "trafficDurationSeconds"):
        if key in payload:
            return float(payload[key]) / 60
    raw = payload.get("duration") or payload.get("routes", [{}])[0].get("duration")
    if isinstance(raw, str) and raw.endswith("s"):
        return float(raw[:-1]) / 60
    if isinstance(raw, (int, float)):
        return float(raw) / 60
    raise ValueError("directions response has no duration")


def true_detour_minutes(direct: dict, first_leg: dict, second_leg: dict) -> float:
    return max(0.0, _duration_minutes(first_leg) + _duration_minutes(second_leg) - _duration_minutes(direct))


def _page_kind(page: dict, candidate: Candidate, card: dict) -> tuple[str, bool]:
    url = page.get("url", "")
    domain = registrable_domain(url)
    if domain in {"instagram.com", "facebook.com"}:
        reasons = set(page.get("identity_reasons", []))
        if page.get("identity_label") == "exact-venue" and reasons.intersection({"official-link", "address", "phone"}):
            return "official_social", False
        return "travel_blog", False
    own_details = _candidate_details({"details": candidate.details})
    own_url = own_details.get("websiteUri") or own_details.get("website") or ""
    own_domain = registrable_domain(own_url)
    if own_url and domain == own_domain:
        if _is_aggregator_domain(own_domain):
            return "generic_web", bool(page.get("roundup"))
        return "official_site", False
    explicit = page.get("source_kind")
    if explicit in {"official", "official_site", "official_menu", "official_social"}:
        if not page.get("_mined_page"):
            return explicit, bool(page.get("roundup"))
        if "official-domain" in page.get("identity_reasons", []):
            return "official_site", False
        return "travel_blog", False
    if explicit and explicit != "generic_web":
        return explicit, bool(page.get("roundup"))
    lowered = f"{url} {page.get('title', '')}".lower()
    if any(term in lowered for term in ("glovo", "deliveroo", "ubereats", "justeat")):
        return "delivery_app", False
    roundup = bool(page.get("roundup")) or any(term in lowered for term in ("best ", "top ", "migliori", "roundup"))
    if any(term in lowered for term in ("gamberorosso", "michelin", "slowfood", "repsol")):
        return "qualified_guide", roundup
    return "travel_blog", roundup


def _claim_text(claim_type: str) -> str:
    return {
        "operational_status": "Operational status at verification",
        "hours_at_arrival": "Opening hours at the arrival window",
        "detour": "Requested detour budget",
        "proximity": "Within the requested travel budget of the anchor",
        "product_inventory": "Requested product availability",
        "counter_service": "Cut-to-order counter service",
        "layout": "Requested layout",
        "quality": "Rated-guide or dated-local-press quality evidence",
        "importance": "Official evidence of event scale and importance",
        "event_schedule": "Official event schedule at arrival",
        "venue_type": "Venue type",
        "rating_signal": "Google rating signal",
    }.get(claim_type, claim_type.replace("_", " ").capitalize())


class Funnel:
    def __init__(self, config: dict[str, Any], adapters: RecordedAdapters, *, now: datetime | None = None) -> None:
        self.config = config
        self.adapters = adapters
        self.now = now or utc_now()
        self.rejected: list[Candidate] = []
        self.search_budget_exhausted = False

    def _call(self, adapter: str, operation: str, request: dict, fn) -> Any:
        return self.adapters.call(adapter, operation, request, fn)

    def stage0_parse(self, raw_input: dict[str, Any]) -> tuple[StructuredRequest, dict]:
        response = self._call("model", "parse", raw_input, lambda: self.adapters.model.run("parse", raw_input))
        parsed = dict(response.get("request", response))
        parsed.setdefault("ask", raw_input.get("ask", ""))
        cli_scope = raw_input.get("scope")
        if cli_scope:
            parsed["scope"] = cli_scope
        if raw_input.get("when") and not parsed.get("arrival_start"):
            parsed["arrival_start"] = raw_input["when"]
            parsed["arrival_end"] = raw_input["when"]
        if raw_input.get("max_detour_min") is not None:
            parsed["max_detour_min"] = raw_input["max_detour_min"]
        parsed["exclusions"] = list(dict.fromkeys([*parsed.get("exclusions", []), *raw_input.get("exclude", [])]))
        request = StructuredRequest.from_dict(parsed)
        allowed_effects = {"required_attribute", "ranking_signal", "search_space"}
        for preference in request.preferences:
            if preference.get("effect_type") not in allowed_effects or not preference.get("effect"):
                raise ValueError("each preference must have one of the three allowed effect types and state its effect")

        card_name = raw_input.get("card") or request.category
        found = find_card(card_name, self.config["paths"]["cards"], self.config["freshness_days"])
        if found:
            card = found[0]
        else:
            card = response.get("ephemeral_card")
            if not card:
                raise ValueError(f"no card for {card_name}, and parse lane did not draft one")
            validate_card(card, self.config["freshness_days"])
            save_ephemeral_card(card, self.config["paths"]["drafts"], self.config["freshness_days"])
        return request, card

    def stage1_sweep(self, request: StructuredRequest, card: dict) -> list[Candidate]:
        payload = {"request": request.to_dict(), "card": card}
        response = self._call("goplaces", "sweep", payload, lambda: self.adapters.places.sweep(request.to_dict(), card))
        if response.get("type_attempt_count", 0) and not response.get("type_success_count", 0):
            raise AdapterError("every configured Places type failed")
        survivors: dict[str, Candidate] = {}
        for raw in response.get("candidates", []):
            candidate = Candidate.from_place(raw)
            if _excluded(candidate, request.exclusions):
                continue
            if candidate.business_status != OPERATIONAL:
                candidate.rejection_reason = "dead_at_sweep"
                candidate.verdict = "rejected"
                self.rejected.append(candidate)
                continue
            if _out_of_scope(candidate, request.scope):
                candidate.rejection_reason = "out_of_scope"
                candidate.verdict = "rejected"
                self.rejected.append(candidate)
                continue
            survivors.setdefault(candidate.place_id, candidate)
        return list(survivors.values())[:20]

    def resolve_anchor(self, request: StructuredRequest) -> StructuredRequest | None:
        if request.scope.get("kind") != "anchor":
            return request
        place = str(request.scope.get("place", ""))
        if not place:
            return None
        resolve_request = {"name": place, "request": request.to_dict()}
        resolved = self._call(
            "goplaces",
            "resolve",
            resolve_request,
            lambda: self.adapters.places.resolve(place, request.to_dict()),
        )
        if not resolved:
            return None
        anchor = Candidate.from_place(resolved)
        location = _location_tuple({"location": anchor.location})
        if not anchor.place_id or location is None:
            return None
        parsed = request.to_dict()
        parsed["scope"] = {
            **request.scope,
            "mode": request.scope.get("mode", "walk"),
            "max_min": float(request.scope.get("max_min", 10)),
            "place_id": anchor.place_id,
            "lat": location[0],
            "lng": location[1],
        }
        parsed["exclusions"] = list(dict.fromkeys([*request.exclusions, anchor.place_id]))
        return StructuredRequest.from_dict(parsed)

    def stage2_qualify(self, candidates: list[Candidate], request: StructuredRequest, card: dict) -> list[Candidate]:
        candidate_rows = [{**candidate.raw, "place_id": candidate.place_id, "name": candidate.name} for candidate in candidates]
        payload = {"request": request.to_dict(), "card": card, "candidates": candidate_rows}
        response = self._call("web", "registry", payload, lambda: self.adapters.web.registry(request.to_dict(), card, candidate_rows))
        self.search_budget_exhausted = self.search_budget_exhausted or bool(response.get("budget_exhausted"))
        by_id = {candidate.place_id: candidate for candidate in candidates}
        weights = card.get("sources", {}).get(request.country, {}) or {}
        for row in response.get("qualifications", []):
            candidate = by_id.get(row.get("place_id"))
            if not candidate:
                continue
            entry_type = row.get("entry_type", "mention")
            source = row.get("source", "")
            candidate.registry.append({**row, "entry_type": entry_type})
            if entry_type == "rated_entry":
                candidate.source_weight += float(weights.get(source, 0))
        for injected in response.get("injected", []):
            resolve_request = {"name": injected["name"], "request": request.to_dict()}
            place = self._call(
                "goplaces",
                "resolve",
                resolve_request,
                lambda item=injected: self.adapters.places.resolve(item["name"], request.to_dict()),
            )
            if not place:
                continue
            candidate = Candidate.from_place(place)
            if _excluded(candidate, request.exclusions):
                continue
            if candidate.business_status != OPERATIONAL:
                candidate.rejection_reason = "dead_injected_at_sweep_gate"
                candidate.verdict = "rejected"
                self.rejected.append(candidate)
                continue
            if _out_of_scope(candidate, request.scope):
                candidate.rejection_reason = "out_of_scope"
                candidate.verdict = "rejected"
                self.rejected.append(candidate)
                continue
            if candidate.place_id not in by_id:
                candidate.registry.append(injected)
                by_id[candidate.place_id] = candidate
        return sorted(by_id.values(), key=lambda item: (item.source_weight, item.rating or 0), reverse=True)

    def stage3_mine(self, candidates: list[Candidate], request: StructuredRequest, card: dict, depth: str) -> dict[str, dict]:
        if depth == "quick":
            mined = {}
            for candidate in candidates[:5]:
                detail_request = {"place_id": candidate.place_id, "local_language": request.local_language}
                details = self._call(
                    "goplaces",
                    "details",
                    detail_request,
                    lambda current=candidate: self.adapters.places.details(current.place_id, request.local_language),
                )
                candidate.detail_payload = details
                payload = {
                    "candidate": {"place_id": candidate.place_id, "name": candidate.name, "details": details},
                    "details": details,
                }
                mined[candidate.place_id] = self._call(
                    "web",
                    "official_pages",
                    payload,
                    lambda current=candidate, current_details=details: self.adapters.web.official_pages(
                        {"place_id": current.place_id, "name": current.name, "details": current_details}, current_details
                    ),
                )
                self.search_budget_exhausted = self.search_budget_exhausted or bool(
                    mined[candidate.place_id].get("budget_exhausted")
                )
            return mined
        mined = {}
        for candidate in candidates[:5]:
            replay = self.adapters.replay
            next_call = replay.calls[replay.index] if replay and replay.index < len(replay.calls) else {}
            legacy_replay = next_call.get("adapter") == "web" and next_call.get("operation") == "mine"
            details = None
            if not legacy_replay:
                detail_request = {"place_id": candidate.place_id, "local_language": request.local_language}
                details = self._call(
                    "goplaces",
                    "details",
                    detail_request,
                    lambda current=candidate: self.adapters.places.details(current.place_id, request.local_language),
                )
                candidate.detail_payload = details
            web_candidate = {"place_id": candidate.place_id, "name": candidate.name}
            if details is not None:
                web_candidate["details"] = details
            official = {"pages": [], "evidence": [], "budget_exhausted": False}
            next_call = replay.calls[replay.index] if replay and replay.index < len(replay.calls) else {}
            has_recorded_official_call = next_call.get("adapter") == "web" and next_call.get("operation") == "official_pages"
            if replay is None or has_recorded_official_call:
                official_payload = {"candidate": web_candidate, "details": details or {}}
                official = self._call(
                    "web",
                    "official_pages",
                    official_payload,
                    lambda current_candidate=web_candidate, current_details=details or {}: self.adapters.web.official_pages(
                        current_candidate, current_details
                    ),
                )
            payload = {"candidate": web_candidate, "request": request.to_dict(), "card": card}
            searched = self._call(
                "web",
                "mine",
                payload,
                lambda current_candidate=web_candidate: self.adapters.web.mine(current_candidate, request.to_dict(), card),
            )
            official_pages = list(official.get("pages", []))
            official_urls = {page.get("url") for page in official_pages if page.get("url")}
            search_pages = [page for page in searched.get("pages", []) if page.get("url") not in official_urls]
            mined[candidate.place_id] = {
                **searched,
                "pages": [*official_pages, *search_pages],
                "evidence": [*official.get("evidence", []), *searched.get("evidence", [])],
                "budget_exhausted": bool(official.get("budget_exhausted") or searched.get("budget_exhausted")),
            }
            self.search_budget_exhausted = self.search_budget_exhausted or bool(
                mined[candidate.place_id].get("budget_exhausted")
            )
        return mined

    def _directions(
        self,
        start: str,
        end: str,
        departure: str | None,
        *,
        start_is_place_id: bool = False,
        end_is_place_id: bool = False,
        mode: str = "drive",
    ) -> dict:
        payload = {
            "start": start,
            "end": end,
            "departure_time": departure,
            "start_is_place_id": start_is_place_id,
            "end_is_place_id": end_is_place_id,
        }
        if mode != "drive":
            payload["mode"] = mode
        return self._call(
            "goplaces",
            "directions",
            payload,
            lambda: self.adapters.places.directions(
                start,
                end,
                departure,
                start_is_place_id=start_is_place_id,
                end_is_place_id=end_is_place_id,
                mode=mode,
            ),
        )

    def _build_ledger(
        self,
        candidate: Candidate,
        request: StructuredRequest,
        card: dict,
        mined: dict,
        details: dict,
        detour: float | None,
        travel_minutes: float | None,
    ) -> ClaimLedger:
        ledger = ClaimLedger()
        claim_specs: dict[str, tuple[str, str, bool]] = {
            "operational_status": ("operational_status", _claim_text("operational_status"), True),
            "hours_at_arrival": ("hours_at_arrival", _claim_text("hours_at_arrival"), bool(request.arrival_start)),
        }
        route_scope = request.scope.get("kind") == "route"
        anchor_scope = request.scope.get("kind") == "anchor"
        if route_scope:
            claim_specs["detour"] = ("detour", _claim_text("detour"), True)
        if anchor_scope:
            claim_specs["proximity"] = ("proximity", _claim_text("proximity"), True)
        required_claim_types = set()
        for raw in request.required_attributes:
            claim_type = raw.get("claim_type") or raw.get("claim_id") or "generic"
            if claim_type == "detour" and not route_scope:
                continue
            if claim_type == "proximity" and not anchor_scope:
                continue
            claim_id = raw.get("claim_id") or _slug(raw.get("text", claim_type))
            text = _claim_text(claim_type) if claim_type in ALLOWED_CLAIM_LABELS else (raw.get("text") or _claim_text(claim_type))
            claim_specs[claim_id] = (claim_type, text, True)
            required_claim_types.add(claim_type)
        for claim_type in card.get("load_bearing_claims", []):
            if claim_type == "detour" and not route_scope:
                continue
            if claim_type == "proximity" and not anchor_scope:
                continue
            if claim_type in required_claim_types:
                continue
            claim_specs.setdefault(claim_type, (claim_type, _claim_text(claim_type), False))
        if candidate.primary_type:
            claim_specs["venue_type"] = ("venue_type", f"Venue type: {candidate.primary_type.replace('_', ' ')}", False)
        if candidate.rating is not None and candidate.review_count is not None:
            claim_specs["rating_signal"] = ("rating_signal", f"Google rating: {candidate.rating} from {candidate.review_count} reviews", False)
        for claim_id, (claim_type, text, required) in claim_specs.items():
            ledger.add_claim(Claim(claim_id=claim_id, text=text, claim_type=claim_type, required=required))

        stamp = self.now.isoformat()
        status_text = f"business_status={details.get('businessStatus') or details.get('business_status')}"
        ledger.add_evidence(EvidenceRecord("places_status", "operational_status", "places_field", "goplaces://details", stamp, stamp, status_text, status_text))
        if request.arrival_start and open_for_window(details, request.arrival_start, request.arrival_end) == HoursWindowStatus.OPEN:
            hours_text = f"opening_hours cover {request.arrival_start} to {request.arrival_end or request.arrival_start}"
            ledger.add_evidence(EvidenceRecord("places_hours", "hours_at_arrival", "places_field", "goplaces://details", stamp, stamp, hours_text, hours_text))
        if detour is not None and request.max_detour_min is not None and detour <= request.max_detour_min:
            detour_text = f"true_detour_minutes={detour:.1f}; budget={request.max_detour_min:.1f}"
            ledger.add_evidence(EvidenceRecord("route_detour", "detour", "computed_route", "goplaces://directions", stamp, stamp, detour_text, detour_text))
        if travel_minutes is not None and travel_minutes <= float(request.scope.get("max_min", 10)):
            travel_text = (
                f"travel_minutes={travel_minutes:.1f}; mode={request.scope.get('mode', 'walk')}; "
                f"budget={float(request.scope.get('max_min', 10)):.1f}"
            )
            ledger.add_evidence(EvidenceRecord("anchor_travel", "proximity", "computed_route", "goplaces://directions", stamp, stamp, travel_text, travel_text))
        if candidate.primary_type:
            venue_text = f"primary_type={candidate.primary_type}"
            ledger.add_evidence(EvidenceRecord("places_type", "venue_type", "places_field", "goplaces://details", stamp, stamp, venue_text, venue_text))
        if candidate.rating is not None and candidate.review_count is not None:
            rating_text = f"rating={candidate.rating}; review_count={candidate.review_count}"
            ledger.add_evidence(EvidenceRecord("places_rating", "rating_signal", "places_field", "goplaces://details", stamp, stamp, rating_text, rating_text))
        for index, row in enumerate(candidate.registry):
            if row.get("entry_type") != "rated_entry" or "quality" not in claim_specs:
                continue
            text = row.get("text", row.get("title", "rated guide entry"))
            ledger.add_evidence(
                EvidenceRecord(
                    f"registry_{index}", "quality", "qualified_guide", row.get("url", ""), stamp,
                    row.get("date", stamp), text, row.get("quote", text), roundup=False,
                )
            )
        rows = list(mined.get("evidence", []))
        for page in mined.get("pages", []):
            if not page.get("claim_id"):
                continue
            page = dict(page)
            page["_mined_page"] = True
            if not page.get("identity_label"):
                identity_candidate = {
                    "place_id": candidate.place_id,
                    "name": candidate.name,
                    "details": candidate.details,
                }
                label, reasons = _identity_label(page, identity_candidate)
                page["identity_label"] = label
                page["identity_reasons"] = reasons
            if page["identity_label"] != "exact-venue":
                continue
            rows.append(page)
        for index, row in enumerate(rows):
            row_claim_id = row.get("claim_id")
            exact_claim = next((claim for claim in ledger.claims if claim.claim_id == row_claim_id), None)
            type_claims = [claim for claim in ledger.claims if claim.claim_type == row_claim_id]
            target_claims = ([exact_claim] if exact_claim else []) + [claim for claim in type_claims if claim is not exact_claim]
            if not target_claims:
                continue
            kind, roundup = _page_kind(row, candidate, card)
            text = row.get("text", "")
            fetched = row.get("retrieved_at") or stamp
            dated = row.get("published_date") or row.get("evidence_date") or fetched
            base_evidence_id = row.get("evidence_id", f"web_{index}")
            for target_index, claim in enumerate(target_claims):
                evidence_id = base_evidence_id if target_index == 0 else f"{base_evidence_id}_{claim.claim_id}"
                ledger.add_evidence(
                    EvidenceRecord(
                        evidence_id=evidence_id,
                        claim_id=claim.claim_id,
                        source_kind=kind,
                        url=row.get("url", ""),
                        fetched_at=fetched,
                        evidence_date=dated,
                        text=text,
                        quote=row.get("quote", ""),
                        polarity=row.get("polarity", "supports"),
                        roundup=roundup,
                        metadata={
                            "title": row.get("title", ""),
                            **(
                                {
                                    "identity_label": row["identity_label"],
                                    "identity_reasons": row.get("identity_reasons", []),
                                }
                                if row.get("_mined_page")
                                else {}
                            ),
                        },
                    )
                )
        for photo_index, row in enumerate(mined.get("photo_responses", [])):
            metadata = row.get("metadata", {}) if isinstance(row, dict) else {}
            if metadata.get("observed_text"):
                continue
            response = row.get("response", {}) if isinstance(row, dict) else {}
            photo_name = metadata.get("name", f"photo-{photo_index + 1}")
            eligible = [
                claim
                for claim in ledger.claims
                if claim.claim_type in {"counter_service", "layout", "product_inventory", "vegetarian_options"}
                and (not metadata.get("claim_id") or metadata.get("claim_id") == claim.claim_id)
            ]
            for claim in eligible:
                photo_text = f"photo_resource={photo_name}"
                ledger.add_evidence(
                    EvidenceRecord(
                        evidence_id=f"photo_{photo_index + 1}_{claim.claim_id}",
                        claim_id=claim.claim_id,
                        source_kind="photo",
                        url=response.get("url") or response.get("photoUri") or f"goplaces://photo/{photo_name}",
                        fetched_at=stamp,
                        evidence_date=metadata.get("evidence_date", stamp),
                        text=photo_text,
                        quote=photo_text,
                        metadata={"photo": response, "question": claim.text},
                    )
                )
        ledger.freeze()
        return ledger

    def stage4_verify(self, candidates: list[Candidate], request: StructuredRequest, card: dict, mined: dict[str, dict]) -> list[Candidate]:
        survivors = []
        direct_route = None
        if request.scope.get("kind") == "route":
            try:
                direct_route = self._directions(request.scope["from"], request.scope["to"], request.arrival_start)
            except AdapterError:
                direct_route = None
        for candidate in candidates[:5]:
            if _excluded(candidate, request.exclusions):
                continue
            payload = {"place_id": candidate.place_id, "local_language": request.local_language}
            detail_payload = candidate.detail_payload
            if detail_payload is None:
                detail_payload = self._call("goplaces", "details", payload, lambda current=candidate: self.adapters.places.details(current.place_id, request.local_language))
            else:
                candidate.detail_payload = None
            en = _detail_body(detail_payload.get("en", {}))
            local = _detail_body(detail_payload.get("local", {}))
            candidate.details = dict(en)
            if isinstance(detail_payload.get("hours_supplement"), dict):
                candidate.details["hours_supplement"] = detail_payload["hours_supplement"]
            photo_rows = []
            for photo in en.get("photos", [])[:10]:
                photo_name = photo.get("name") if isinstance(photo, dict) else str(photo)
                if not photo_name:
                    continue
                photo_request = {"photo_name": photo_name}
                photo_response = self._call("goplaces", "photo", photo_request, lambda name=photo_name: self.adapters.places.photo(name))
                photo_rows.append({"metadata": photo, "response": photo_response})
                if isinstance(photo, dict) and photo.get("claim_id") and photo.get("observed_text"):
                    mined.setdefault(candidate.place_id, {}).setdefault("evidence", []).append(
                        {
                            "evidence_id": photo.get("evidence_id", f"photo_{len(photo_rows)}"),
                            "claim_id": photo["claim_id"],
                            "source_kind": "photo",
                            "url": photo_response.get("url", f"goplaces://photo/{photo_name}"),
                            "retrieved_at": self.now.isoformat(),
                            "evidence_date": photo.get("evidence_date", self.now.isoformat()),
                            "text": photo["observed_text"],
                            "quote": photo["observed_text"],
                            "polarity": photo.get("polarity", "supports"),
                        }
                    )
            if photo_rows:
                mined.setdefault(candidate.place_id, {})["photo_responses"] = photo_rows
            if not _same_identity(candidate.place_id, en, local):
                candidate.rejection_reason = "identity_mismatch"
            statuses = {en.get("businessStatus") or en.get("business_status"), local.get("businessStatus") or local.get("business_status")}
            if statuses != {OPERATIONAL}:
                candidate.rejection_reason = "dead_or_status_conflict_at_deep_verify"
            hours_status = (
                open_for_window(candidate.details, request.arrival_start, request.arrival_end)
                if request.arrival_start
                else HoursWindowStatus.UNKNOWN
            )
            if hours_status == HoursWindowStatus.CLOSED:
                candidate.rejection_reason = "closed_at_arrival_window"
            detour = None
            if direct_route is not None:
                try:
                    first = self._directions(
                        request.scope["from"],
                        candidate.place_id,
                        request.arrival_start,
                        end_is_place_id=True,
                    )
                    second = self._directions(
                        candidate.place_id,
                        request.scope["to"],
                        request.arrival_start,
                        start_is_place_id=True,
                    )
                    detour = true_detour_minutes(direct_route, first, second)
                except AdapterError:
                    detour = None
                if (
                    candidate.rejection_reason is None
                    and detour is not None
                    and request.max_detour_min is not None
                    and detour > request.max_detour_min
                ):
                    candidate.rejection_reason = "detour_over_budget"
                    candidate.minutes = detour
            travel_minutes = None
            if request.scope.get("kind") == "anchor":
                try:
                    travel = self._directions(
                        request.scope["place_id"],
                        candidate.place_id,
                        request.arrival_start,
                        start_is_place_id=True,
                        end_is_place_id=True,
                        mode=request.scope.get("mode", "walk"),
                    )
                    travel_minutes = _duration_minutes(travel)
                except (AdapterError, ValueError):
                    travel_minutes = None
                if (
                    candidate.rejection_reason is None
                    and travel_minutes is not None
                    and travel_minutes > float(request.scope.get("max_min", 10))
                ):
                    candidate.rejection_reason = "travel_over_budget"
                    candidate.minutes = travel_minutes
            if candidate.rejection_reason:
                candidate.verdict = "rejected"
                self.rejected.append(candidate)
                continue
            candidate.ledger = self._build_ledger(
                candidate,
                request,
                card,
                mined.get(candidate.place_id, {}),
                candidate.details,
                detour,
                travel_minutes,
            )
            survivors.append(candidate)
        return survivors

    def stage5_judge(self, candidates: list[Candidate], card: dict) -> None:
        freshness = dict(self.config["freshness_days"])
        freshness.update(card.get("freshness_overrides", {}))
        for candidate in candidates:
            photo_evidence = [row.to_dict() for row in candidate.ledger.evidence if row.source_kind == "photo"]
            photo_judgments = []
            if photo_evidence:
                triage_payload = {"place_id": candidate.place_id, "photos": photo_evidence}
                triage = self._call(
                    "model",
                    "photo_triage",
                    triage_payload,
                    lambda: self.adapters.model.run("photo_triage", triage_payload),
                )
                read_payload = {
                    "place_id": candidate.place_id,
                    "photos": photo_evidence,
                    "triage": triage,
                    "instruction": "Answer only the listed claim questions for inspected photos.",
                }
                photo_read = self._call(
                    "model",
                    "photo_read",
                    read_payload,
                    lambda: self.adapters.model.run("photo_read", read_payload),
                )
                photo_judgments = photo_read.get("judgments", [])
            payload = {"place_id": candidate.place_id, "ledger": candidate.ledger.to_dict(), "instruction": "Refute each claim. Return literal quotes only."}
            response = self._call("model", "judge", payload, lambda current=candidate: self.adapters.model.run("judge", payload))
            candidate.ledger.compute([*response.get("judgments", []), *photo_judgments], freshness, now=self.now)

    def _verdict(self, candidate: Candidate, card: dict, contact_drafts: bool, language: str) -> str:
        claims = candidate.ledger.claims
        required = [claim for claim in claims if claim.required]
        if any(claim.status == ClaimStatus.CONFLICT for claim in required):
            candidate.rejection_reason = "unresolved_evidence_conflict"
            return "rejected"
        if all(claim.status == ClaimStatus.SUPPORTED for claim in required):
            return "cleared"
        if any(claim.status == ClaimStatus.REFUTED for claim in required):
            return "near_miss"
        layout_missing = any(claim.required and claim.claim_type in {"counter_service", "layout"} and claim.status != ClaimStatus.SUPPORTED for claim in claims)
        if layout_missing and candidate.primary_type in {"wine_bar", "bar", "sandwich_shop"}:
            return "near_miss"
        hours_missing = next(
            (claim for claim in claims if claim.required and claim.claim_type == "hours_at_arrival" and claim.status != ClaimStatus.SUPPORTED),
            None,
        )
        if hours_missing and contact_drafts:
            candidate.proposed_contact = {
                "channel": "WhatsApp or phone",
                "message": HOURS_CONTACT_QUESTIONS.get(language, HOURS_CONTACT_QUESTIONS["en"]),
                "translation": HOURS_CONTACT_QUESTIONS["en"],
                "settles_claim": hours_missing.claim_id,
            }
            return "unconfirmed"
        if layout_missing and contact_drafts:
            missing_claim = next(
                claim
                for claim in claims
                if claim.required and claim.claim_type in {"counter_service", "layout"} and claim.status != ClaimStatus.SUPPORTED
            )
            questions = card.get("contact_questions", {}).get(missing_claim.claim_type, {})
            message = questions.get(language) or questions.get("en")
            translation = questions.get("en")
            if message and translation:
                candidate.proposed_contact = {
                    "channel": "WhatsApp or phone",
                    "message": message,
                    "translation": translation,
                    "settles_claim": missing_claim.claim_id,
                }
        return "unconfirmed"

    def stage6_render(
        self,
        candidates: list[Candidate],
        request: StructuredRequest,
        card: dict,
        contact_drafts: bool,
        refusal_reason: str | None = None,
    ) -> dict[str, Any]:
        for candidate in candidates:
            candidate.verdict = self._verdict(candidate, card, contact_drafts, request.local_language)
        assemble_payload = {
            "candidates": [
                {
                    "place_id": candidate.place_id,
                    "verdict": candidate.verdict,
                    "claims": [claim.to_dict() for claim in candidate.ledger.claims],
                }
                for candidate in candidates
            ],
            "instruction": "Phrase only these frozen rows. Do not add claims or appeal language.",
        }
        self._call("model", "assemble", assemble_payload, lambda: self.adapters.model.run("assemble", assemble_payload))
        output_candidates = []
        for candidate in [*candidates, *self.rejected]:
            if candidate.ledger:
                claims = [claim.to_dict() for claim in candidate.ledger.claims]
                rendered = [f"{claim.text}: {claim.status.value} ({claim.evidence_clause})" for claim in candidate.ledger.claims]
                rendered_ids = [claim.claim_id for claim in candidate.ledger.claims]
            else:
                claims = []
                rendered = []
                rendered_ids = []
            output_candidates.append(
                {
                    "name": candidate.name,
                    "place_id": candidate.place_id,
                    "verdict": candidate.verdict or "rejected",
                    "reason": candidate.rejection_reason,
                    "claims": claims,
                    "rendered_claims": rendered,
                    "rendered_claim_ids": rendered_ids,
                    "proposed_contact": candidate.proposed_contact,
                }
            )
        refusal = not any(row["verdict"] == "cleared" for row in output_candidates)
        human_lines = []
        for row in output_candidates:
            human_lines.append(f"{row['name']}: {row['verdict']}")
            human_lines.extend(f"  {text}" for text in row["rendered_claims"])
            if row["reason"]:
                human_lines.append(f"  Rejected: {row['reason'].replace('_', ' ')}")
        if refusal:
            if refusal_reason:
                human_lines.append(f"Refused: {refusal_reason.replace('_', ' ')}")
            human_lines.append("Nothing clears the evidence bar.")
        for preference in request.preferences:
            human_lines.append(f"Preference: {preference.get('directive', '')} -> {preference['effect']}")
        if self.search_budget_exhausted:
            human_lines.append("Search budget reached; evidence may be incomplete.")
        widen_options = [
            {
                "anchor": "increase the minutes budget",
                "route": "increase the detour budget",
            }.get(request.scope.get("kind"), "increase the radius"),
            "allow cousin categories",
            "ask venues directly",
        ]
        scope_kind = request.scope.get("kind")
        widen_reason = "travel_over_budget" if scope_kind == "anchor" else "detour_over_budget"
        widen_mode = request.scope.get("mode", "walk") if scope_kind == "anchor" else "drive"
        widen_candidates = []
        if refusal and scope_kind in {"anchor", "route"}:
            widen_candidates = [
                {
                    "name": candidate.name,
                    "place_id": candidate.place_id,
                    "minutes": candidate.minutes,
                    "mode": widen_mode,
                }
                for candidate in sorted(
                    (
                        candidate
                        for candidate in self.rejected
                        if candidate.rejection_reason == widen_reason and candidate.minutes is not None
                    ),
                    key=lambda candidate: candidate.minutes,
                )[:3]
            ]
        if refusal:
            human_lines.append("Widen options: " + "; ".join(widen_options))
            if widen_candidates:
                outside = ", ".join(
                    f"{candidate['name']} ({candidate['minutes']:.0f} min {candidate['mode']})"
                    for candidate in widen_candidates
                )
                human_lines.append(f"Just outside the budget: {outside}")
        packet = {
            "request": request.to_dict(),
            "exclusions_applied": list(request.exclusions),
            "preferences_applied": list(request.preferences),
            "candidates": output_candidates,
            "refusal": refusal,
            "reason": refusal_reason if refusal else None,
            "budget_exhausted": self.search_budget_exhausted,
            "widen_options": widen_options if refusal else [],
            "widen_candidates": widen_candidates,
            "human": "\n".join(human_lines),
        }
        return packet

    def run(self, raw_input: dict[str, Any]) -> dict[str, Any]:
        request, card = self.stage0_parse(raw_input)
        resolved_request = self.resolve_anchor(request)
        if resolved_request is None:
            return self.stage6_render([], request, card, bool(raw_input.get("contact_drafts")), "anchor_unresolved")
        request = resolved_request
        candidates = self.stage1_sweep(request, card)
        candidates = self.stage2_qualify(candidates, request, card)
        mined = self.stage3_mine(candidates, request, card, raw_input.get("depth", "full"))
        candidates = self.stage4_verify(candidates, request, card, mined)
        self.stage5_judge(candidates, card)
        return self.stage6_render(candidates, request, card, bool(raw_input.get("contact_drafts")))
