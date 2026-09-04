from __future__ import annotations

import json
import os
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from html.parser import HTMLParser
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from .evidence import registrable_domain


class AdapterError(RuntimeError):
    pass


ANCHOR_SPEEDS_M_PER_MIN = {
    "walk": 80,
    "bicycle": 250,
    "drive": 600,
    "transit": 350,
}

SOCIAL_DOMAINS = {"instagram.com", "facebook.com"}
AGGREGATOR_DOMAINS = {
    "privateaser",
    "thefork",
    "eltenedor",
    "opentable",
    "resy",
    "tripadvisor",
    "yelp",
    "google",
    "facebook",
    "instagram",
    "linktr.ee",
}


def _is_aggregator_domain(domain: str) -> bool:
    return domain in AGGREGATOR_DOMAINS or domain.partition(".")[0] in AGGREGATOR_DOMAINS


def _detail_body(payload: dict) -> dict:
    for key in ("place", "result"):
        if isinstance(payload.get(key), dict):
            return payload[key]
    return payload


def _candidate_details(candidate: dict, language: str = "en") -> dict:
    details = candidate.get("details", {})
    if isinstance(details, dict) and isinstance(details.get(language), dict):
        return _detail_body(details[language])
    return _detail_body(details) if isinstance(details, dict) else {}


def _display_name(body: dict) -> str:
    display = body.get("displayName") or body.get("display_name") or ""
    return str(display.get("text", "")) if isinstance(display, dict) else str(display)


def _field(candidate: dict, *names: str) -> Any:
    details = _candidate_details(candidate)
    for source in (candidate, details):
        for name in names:
            if source.get(name) not in (None, ""):
                return source[name]
    return None


def locality_from_candidate(candidate: dict) -> str:
    address = str(_field(candidate, "formattedAddress", "formatted_address", "address") or "")
    parts = [part.strip() for part in address.split(",") if part.strip()]
    return parts[-2] if len(parts) >= 2 else (parts[-1] if parts else "")


def _normalized(value: str) -> str:
    return " ".join(str(value).casefold().split())


def _phone(value: Any) -> str:
    return "".join(re.findall(r"\d", str(value or "")))


def _location(candidate: dict) -> tuple[float, float] | None:
    location = _field(candidate, "location") or {}
    if not isinstance(location, dict):
        return None
    lat = location.get("latitude", location.get("lat"))
    lng = location.get("longitude", location.get("lng"))
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


def _distance_m(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_lat, left_lng = map(radians, left)
    right_lat, right_lng = map(radians, right)
    delta_lat = right_lat - left_lat
    delta_lng = right_lng - left_lng
    value = sin(delta_lat / 2) ** 2 + cos(left_lat) * cos(right_lat) * sin(delta_lng / 2) ** 2
    return 2 * 6371008.8 * asin(min(1.0, sqrt(value)))


def _page_coordinates(page: dict) -> list[tuple[float, float]]:
    location = page.get("location") or {}
    structured = []
    if isinstance(location, dict):
        lat = location.get("latitude", location.get("lat"))
        lng = location.get("longitude", location.get("lng"))
        if lat is not None and lng is not None:
            structured.append((float(lat), float(lng)))
    text = f"{page.get('url', '')} {page.get('title', '')} {page.get('text', '')}"
    pairs = re.findall(
        r"(?:@|[?&](?:q|ll)=|(?:coordinates|latitude)[^\d-]*)(-?\d{1,2}\.\d+)[,\s]+(?:longitude[^\d-]*)?(-?\d{1,3}\.\d+)",
        text,
        re.I,
    )
    return structured + [(float(lat), float(lng)) for lat, lng in pairs if -90 <= float(lat) <= 90 and -180 <= float(lng) <= 180]


def _page_place_ids(page: dict) -> set[str]:
    text = f"{page.get('url', '')} {page.get('text', '')}"
    patterns = (
        r"(?:query_place_id|place_id)[=:/\s]+([A-Za-z0-9_-]{3,})",
        r"(?:places/|!1s)([A-Za-z0-9_-]{3,})",
        r"\b(ChIJ[A-Za-z0-9_-]+)\b",
    )
    ids = {match for pattern in patterns for match in re.findall(pattern, text, re.I)}
    explicit = page.get("place_id") or page.get("placeId")
    if explicit:
        ids.add(str(explicit).removeprefix("places/"))
    return ids


def _identity_label(page: dict, candidate: dict) -> tuple[str, list[str]]:
    haystack = _normalized(f"{page.get('url', '')} {page.get('title', '')} {page.get('text', '')}")
    reasons: list[str] = []
    place_id_contradiction = False
    soft_contradiction = False

    place_id = str(candidate.get("place_id") or candidate.get("id") or "").removeprefix("places/")
    page_ids = _page_place_ids(page)
    folded_page_ids = {page_id.casefold() for page_id in page_ids}
    if place_id and (place_id.casefold() in folded_page_ids or ("maps" in str(page.get("url", "")).casefold() and place_id.casefold() in haystack)):
        reasons.append("place-id")
    if page_ids and place_id and any(page_id.casefold() != place_id.casefold() for page_id in page_ids):
        place_id_contradiction = True

    candidate_location = _location(candidate)
    coordinates = _page_coordinates(page)
    if candidate_location and coordinates:
        if any(_distance_m(candidate_location, point) <= 150 for point in coordinates):
            reasons.append("coordinates")
        else:
            soft_contradiction = True

    address = _normalized(str(_field(candidate, "formattedAddress", "formatted_address", "address") or ""))
    page_address = _normalized(str(page.get("formattedAddress") or page.get("address") or ""))
    if address and (address in haystack or (page_address and (address in page_address or page_address in address))):
        reasons.append("address")
    elif address and page_address:
        soft_contradiction = True

    candidate_phone = _phone(_field(candidate, "nationalPhoneNumber", "internationalPhoneNumber", "phone", "phone_number"))
    page_phones = {_phone(match) for match in re.findall(r"\+?[\d][\d\s()./-]{5,}\d", f"{page.get('title', '')} {page.get('text', '')}")}
    page_phones = {phone for phone in page_phones if len(phone) >= 7}
    if candidate_phone and any(phone[-7:] == candidate_phone[-7:] for phone in page_phones):
        reasons.append("phone")
    if candidate_phone and page_phones and any(phone[-7:] != candidate_phone[-7:] for phone in page_phones):
        soft_contradiction = True

    website = str(_field(candidate, "websiteUri", "website") or "")
    page_domain = registrable_domain(str(page.get("url", "")))
    website_domain = registrable_domain(website)
    if website_domain and not _is_aggregator_domain(website_domain) and page_domain == website_domain:
        reasons.append("official-domain")

    social_urls = candidate.get("official_social_urls", []) or []
    normalized_url = str(page.get("url", "")).rstrip("/").casefold()
    if any(normalized_url == str(url).rstrip("/").casefold() for url in social_urls):
        reasons.append("official-link")

    if place_id_contradiction:
        return "ambiguous", reasons
    if {"place-id", "phone", "official-domain", "official-link"}.intersection(reasons):
        return "exact-venue", reasons
    if soft_contradiction:
        return "ambiguous", reasons
    if reasons:
        return "exact-venue", reasons
    names = {
        _normalized(str(candidate.get("name") or _display_name(_candidate_details(candidate)))),
        _normalized(_display_name(_candidate_details(candidate, "local"))),
    }
    names.discard("")
    locality = _normalized(locality_from_candidate(candidate))
    if names and locality and any(name in haystack for name in names) and locality in haystack:
        return "area-level", []
    return "ambiguous", []


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
    def __init__(self, endpoint: str, api_key: str | None = None, retrieval: dict[str, int] | None = None) -> None:
        self.endpoint = endpoint
        self.api_key = api_key or os.environ.get("EXA_API_KEY")
        limits = retrieval or {}
        self.max_queries_per_candidate = int(limits.get("max_queries_per_candidate", 8))
        self.max_results_per_query = int(limits.get("max_results_per_query", 5))
        self.max_pages_per_run = int(limits.get("max_pages_per_run", 60))
        self._pages_retrieved = 0

    def _search(self, query: str) -> dict:
        if not self.api_key:
            raise AdapterError("EXA_API_KEY is required for a live run")
        body = json.dumps({"query": query, "numResults": self.max_results_per_query, "contents": {"text": True}}).encode()
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
        searches = 0
        budget_exhausted = False
        localities = (locality_from_candidate(candidate) for candidate in candidates or [])
        locality = next((value for value in localities if value), "")
        for source, weight in (card.get("sources", {}).get(country, {}) or {}).items():
            if searches >= self.max_queries_per_candidate or self._pages_retrieved >= self.max_pages_per_run:
                budget_exhausted = True
                break
            query = " ".join(
                part for part in (source.replace("_", " "), request.get("category", ""), request.get("ask", ""), locality) if part
            )
            response = self._search(query)
            searches += 1
            rows.append({"source": source, "response": response})
            results = list(response.get("results", []))
            remaining = max(0, self.max_pages_per_run - self._pages_retrieved)
            selected = results[: self.max_results_per_query][:remaining]
            if len(results) > len(selected):
                budget_exhausted = True
            self._pages_retrieved += len(selected)
            for result in selected:
                title = str(result.get("title", ""))
                title_folded = title.strip().casefold()
                text = f"{title} {result.get('text', '')}".casefold()
                list_page = bool(title_folded and title_folded[0].isdigit()) or any(
                    term in title_folded
                    for term in ("best ", "top ", "migliori", "mejores", "guide to", "guía de", "roundup", "ranking")
                )
                explicit_rated = result.get("entry_type") == "rated_entry" and bool(result.get("rating") or result.get("score"))
                declared_domains = {
                    registrable_domain(f"https://{domain}")
                    for domain in card.get("source_domains", {}).get(source, [])
                }
                domain_matches = registrable_domain(str(result.get("url", ""))) in declared_domains
                for candidate in candidates or []:
                    candidate_name = candidate.get("name", "").casefold()
                    if candidate_name in text:
                        entry_type = "rated_entry" if explicit_rated or (
                            domain_matches
                            and candidate_name in title_folded
                            and not list_page
                            and weight >= 0.5
                        ) else "mention"
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
                    entity_name = str(result["entity_name"])
                    entry_type = "rated_entry" if explicit_rated or (
                        domain_matches
                        and entity_name.casefold() in title_folded
                        and not list_page
                        and weight >= 0.5
                    ) else "mention"
                    injected.append(
                        {
                            "name": entity_name,
                            "source": source,
                            "entry_type": entry_type,
                            "url": result.get("url", ""),
                        }
                    )
        return {
            "sources": rows,
            "qualifications": qualifications,
            "injected": injected,
            "budget_exhausted": budget_exhausted,
        }

    def mine(self, candidate: dict, request: dict, card: dict) -> dict[str, Any]:
        queries: list[tuple[str, str]] = []
        templates = card.get("query_templates", [])
        city = request.get("scope", {}).get("city") or locality_from_candidate(candidate)
        local_name = _display_name(_candidate_details(candidate, "local"))
        english_name = str(candidate.get("name") or _display_name(_candidate_details(candidate)))
        alias = local_name if local_name and local_name.casefold() != english_name.casefold() else ""
        for template in templates:
            if isinstance(template, dict):
                template_text = template.get("template") or template.get("query") or ""
                claim_id = template.get("claim_id") or template.get("claim_type") or "quality"
            else:
                template_text = template
                claim_id = "quality"
            query = template_text.format(name=english_name, city=city, category=request.get("category", ""))
            queries.append((" ".join(part for part in (query, alias) if part), claim_id))
        for attribute in request.get("required_attributes", []):
            attribute_text = attribute.get("text") or attribute.get("claim_id") or attribute.get("claim_type", "")
            claim_id = attribute.get("claim_id") or re.sub(r"[^a-z0-9]+", "_", attribute_text.lower()).strip("_") or "claim"
            queries.append((" ".join(part for part in (english_name, attribute_text, alias) if part), claim_id))
        pages = []
        searches = 0
        budget_exhausted = False
        for query_index, (query, claim_id) in enumerate(queries):
            if searches >= self.max_queries_per_candidate or self._pages_retrieved >= self.max_pages_per_run:
                budget_exhausted = True
                break
            result = self._search(query)
            searches += 1
            results = list(result.get("results", []))
            remaining = max(0, self.max_pages_per_run - self._pages_retrieved)
            selected = results[: self.max_results_per_query][:remaining]
            if len(results) > len(selected) or (query_index + 1 < len(queries) and self._pages_retrieved + len(selected) >= self.max_pages_per_run):
                budget_exhausted = True
            self._pages_retrieved += len(selected)
            for row in selected:
                page = {
                    "query": query,
                    "claim_id": claim_id,
                    "url": row.get("url", ""),
                    "title": row.get("title", ""),
                    "text": row.get("text", ""),
                    "published_date": row.get("publishedDate"),
                    "retrieved_at": result.get("retrieved_at"),
                    "source_kind": row.get("source_kind", "generic_web"),
                    "formattedAddress": row.get("formattedAddress"),
                    "address": row.get("address"),
                    "place_id": row.get("place_id") or row.get("placeId"),
                    "location": row.get("location"),
                }
                label, reasons = _identity_label(page, candidate)
                page["identity_label"] = label
                page["identity_reasons"] = reasons
                pages.append(page)
        return {"pages": pages, "budget_exhausted": budget_exhausted}

    def official_pages(self, candidate: dict, details: dict) -> dict[str, Any]:
        en = details.get("en", details)
        if isinstance(en, dict) and isinstance(en.get("result"), dict):
            en = en["result"]
        website = en.get("websiteUri") or en.get("website") if isinstance(en, dict) else None
        if not website:
            return {"pages": [], "evidence": [], "budget_exhausted": False}

        pages = []
        website_source_kind = "generic_web" if _is_aggregator_domain(registrable_domain(str(website))) else "official"
        menu_url = None
        social_urls: list[str] = []
        budget_exhausted = False
        for url in (str(website),):
            try:
                request = Request(url, headers={"Accept": "text/html,text/plain", "User-Agent": "osusume/0.1"})
                with urlopen(request, timeout=15) as response:
                    content_type = response.headers.get("Content-Type", "text/html").split(";", 1)[0].strip().lower()
                    if not (content_type.startswith("text/") or content_type == "application/xhtml+xml"):
                        continue
                    charset = response.headers.get_content_charset() or "utf-8"
                    body = response.read(512 * 1024).decode(charset, errors="replace")
                    final_url = response.geturl() if hasattr(response, "geturl") else url
                parser = _OfficialPageParser()
                parser.feed(body)
                text = " ".join(" ".join(parser.text).split())
                page = {
                    "url": final_url,
                    "title": " ".join(" ".join(parser.title).split()),
                    "text": text,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "source_kind": website_source_kind,
                    "claim_id": "product_inventory",
                }
                label, reasons = _identity_label(page, {**candidate, "details": details})
                page["identity_label"] = label
                page["identity_reasons"] = reasons
                pages.append(page)
                menu_terms = ("menu", "carta", "drinks", "cocktails", "cócteles", "còctels", "bebidas", "vinos", "wine list")
                for href, link_text in parser.links:
                    linked_url = urljoin(final_url, href)
                    if registrable_domain(linked_url) in SOCIAL_DOMAINS and linked_url not in social_urls:
                        social_urls.append(linked_url)
                    path = unquote(urlparse(linked_url).path).casefold()
                    haystack = f"{re.sub(r'[-_]+', ' ', path)} {link_text.casefold()}"
                    if menu_url is None and any(term in haystack for term in menu_terms):
                        menu_url = linked_url
            except Exception:
                continue

        if menu_url and menu_url != pages[0]["url"]:
            try:
                request = Request(menu_url, headers={"Accept": "text/html,text/plain", "User-Agent": "osusume/0.1"})
                with urlopen(request, timeout=15) as response:
                    content_type = response.headers.get("Content-Type", "text/html").split(";", 1)[0].strip().lower()
                    if content_type.startswith("text/") or content_type == "application/xhtml+xml":
                        charset = response.headers.get_content_charset() or "utf-8"
                        body = response.read(512 * 1024).decode(charset, errors="replace")
                        final_url = response.geturl() if hasattr(response, "geturl") else menu_url
                    else:
                        body = ""
                if body:
                    parser = _OfficialPageParser()
                    parser.feed(body)
                    page = {
                        "url": final_url,
                        "title": " ".join(" ".join(parser.title).split()),
                        "text": " ".join(" ".join(parser.text).split()),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "source_kind": website_source_kind,
                        "claim_id": "product_inventory",
                    }
                    label, reasons = _identity_label(page, {**candidate, "details": details})
                    page["identity_label"] = label
                    page["identity_reasons"] = reasons
                    pages.append(page)
            except Exception:
                pass
        for social_url in social_urls:
            if self._pages_retrieved >= self.max_pages_per_run:
                budget_exhausted = True
                break
            try:
                request = Request(social_url, headers={"Accept": "text/html,text/plain", "User-Agent": "osusume/0.1"})
                with urlopen(request, timeout=15) as response:
                    content_type = response.headers.get("Content-Type", "text/html").split(";", 1)[0].strip().lower()
                    if not (content_type.startswith("text/") or content_type == "application/xhtml+xml"):
                        continue
                    charset = response.headers.get_content_charset() or "utf-8"
                    body = response.read(512 * 1024).decode(charset, errors="replace")
                    final_url = response.geturl() if hasattr(response, "geturl") else social_url
                parser = _OfficialPageParser()
                parser.feed(body)
                self._pages_retrieved += 1
                base_page = {
                    "url": final_url,
                    "title": " ".join(" ".join(parser.title).split()),
                    "text": " ".join(" ".join(parser.text).split()),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "source_kind": "official_social",
                }
                social_candidate = {
                    **candidate,
                    "details": details,
                    "official_social_urls": [social_url, final_url],
                }
                label, reasons = _identity_label(base_page, social_candidate)
                base_page["identity_label"] = label
                base_page["identity_reasons"] = reasons
                for claim_id in ("product_inventory", "hours_at_arrival"):
                    pages.append({**base_page, "claim_id": claim_id})
            except Exception:
                continue
        return {"pages": pages, "evidence": [], "budget_exhausted": budget_exhausted}


class _OfficialPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.title: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._hidden_depth = 0
        self._in_title = False
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._hidden_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and self._hidden_depth == 0:
            self._link_href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._link_href is not None:
            self.links.append((self._link_href, " ".join(self._link_text)))
            self._link_href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        self.text.append(data)
        if self._in_title:
            self.title.append(data)
        if self._link_href is not None:
            self._link_text.append(data)


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
        replay_request = json.loads(json.dumps(request, default=str))
        if recorded_request and recorded_request != replay_request:
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
