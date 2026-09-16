#!/usr/bin/env python3
"""Smoke-test model lane: routes osusume slots through the local claude CLI.

Reads {"slot": ..., "payload": ...} on stdin, prints one JSON object.
Photo slots return empty judgments (text lane cannot inspect images), so
photo-dependent claims fail closed to unknown instead of being invented.
"""
import json
import os
import re
import shutil
import subprocess
import sys

MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5"


def claude_binary() -> str:
    """Prefer the account-slot wrapper: a bare ``claude`` needs the keychain,
    which is unreachable from a remote-exec SSH session."""
    override = os.environ.get("CLAUDE_LANE_BIN")
    if override:
        return override
    for name in ("claude-headless", "claude"):
        found = shutil.which(name)
        if found:
            return found
    return "claude"

PARSE_SCHEMA = {
    "request": {
        "ask": "str", "category": "one lowercase token, e.g. ceramics/restaurant/hotel",
        "country": "ISO2", "local_language": "ISO2",
        "required_attributes": [{"claim_id": "snake_case", "text": "objectively checkable venue fact in English", "claim_type": "one of: product_inventory,counter_service,layout,quality,prices,event_schedule,generic", "required": True, "synonyms": ["English and local-language terms that mean the required attribute"], "_rules": "a required attribute is an objectively checkable fact about the venue: a product or drink it sells, a service it performs, a physical feature, or a schedule; it qualifies if a photo, menu, official page, or Places field could settle it yes or no; subjective character, mood, style, price feel, and crowd words are NEVER required attributes (including upscale, quirky, cosy, romantic, lively, hip, authentic, not touristy, and hidden gem), and instead go into preferences with effect_type ranking_signal; an either/or taste phrase such as upscale or quirky is one ranking signal, never two required attributes, and an OR must never be split into several requirements; a concrete thing named inside a taste phrase still counts, so craft cocktail menu is checkable as product_inventory and stays required; never add an opening-hours attribute (the arrival window covers hours); event_schedule is ONLY for recurring events like markets/fairs"}],
        "scope": {"city": "named city from the ask, when present"}, "arrival_start": "RFC3339 or null", "arrival_end": "RFC3339 or null",
        "stay": {"check_in": "YYYY-MM-DD", "check_out": "YYYY-MM-DD", "adults": "int, default 2"},
        "hotel_filters": {"min_stars": "number or null", "max_stars": "number or null", "min_score": "number or null", "pets": "bool or null", "breakfast": "bool or null", "free_cancellation": "bool or null", "hot_tub": "bool or null"},
        "exclusions": [], "preferences": [{"text": "the user preference", "effect_type": "required_attribute|ranking_signal|search_space", "effect": "what it mechanically does"}],
    },
    "ephemeral_card": {
        "category": "same token as request.category",
        "country": "ISO2",
        "languages": {"en": ["english search terms"], "<local iso2>": ["local-language search terms"]},
        "places_types": ["ONLY valid Places API (New) primary types, e.g. restaurant, store, art_gallery, lodging, hotel, tourist_attraction; NEVER point_of_interest or establishment"],
        "query_templates": ["'{name} {city} <local-language evidence query>'"],
        "load_bearing_claims": ["MUST include operational_status, hours_at_arrival, detour; add category-relevant ones like quality, product_inventory"],
        "event_shaped": False,
    },
}

def ask_claude(prompt: str) -> dict:
    binary = claude_binary()
    out = subprocess.run(
        [binary, "-p", "--model", MODEL, "--output-format", "json"],
        input=prompt, capture_output=True, text=True, timeout=240,
    )
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip()[:300] or f"exit {out.returncode}, no output"
        raise SystemExit(f"claude lane failed ({binary}): {detail}")
    result = json.loads(out.stdout)
    usage = result.get("usage") or {}
    print(
        f"claude lane: model={MODEL} api_ms={result.get('duration_api_ms')} "
        f"in={usage.get('input_tokens')} cache_read={usage.get('cache_read_input_tokens')} "
        f"out={usage.get('output_tokens')} cost={result.get('total_cost_usd')}",
        file=sys.stderr,
    )
    text = result["result"]
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0) if match else text)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from osusume.evidence import EvidenceRecord  # noqa: E402
from osusume.funnel import _structured_evidence  # noqa: E402


def _settled_by_engine(row: dict) -> bool:
    """Rows the engine accepts literally (see funnel.STRUCTURED_EVIDENCE_KINDS)."""
    try:
        record = EvidenceRecord(
            row.get("evidence_id", ""), row.get("claim_id", ""), row.get("source_kind", ""), row.get("url", ""),
            row.get("fetched_at", ""), row.get("evidence_date", ""), row.get("text", ""), row.get("quote", ""),
            metadata=row.get("metadata") or {},
        )
    except Exception:  # noqa: BLE001 - an odd row simply goes to the judge
        return False
    return _structured_evidence(record)


def compact_ledger(payload: dict) -> dict:
    """Official pages are attached once per claim, so the same page text can
    appear six times in one ledger. Send each distinct text once under
    ``texts`` and point the rows at it; quotes stay verbatim substrings."""
    ledger = payload.get("ledger") or {}
    rows = ledger.get("evidence") or []
    texts: dict[str, str] = {}
    keys: dict[str, str] = {}
    compact_rows = []
    for row in rows:
        if _settled_by_engine(row):
            continue  # nothing for the judge to read
        text = row.get("text") or ""
        if len(text) < 200:
            compact_rows.append(row)
            continue
        key = keys.get(text)
        if key is None:
            key = f"text_{len(texts) + 1}"
            keys[text] = key
            texts[key] = text
        compact_rows.append({**{k: v for k, v in row.items() if k != "quote"}, "text": f"@{key}"})
    compact = {**payload, "ledger": {**ledger, "evidence": compact_rows}}
    if texts:
        compact["texts"] = texts
        compact["texts_note"] = (
            "An evidence text written as @text_N refers to texts[text_N]. Your quote must be a verbatim "
            "substring of that entry's text; never answer with @text_N itself."
        )
    return compact


def main() -> None:
    job = json.load(sys.stdin)
    slot, payload = job.get("slot", ""), job.get("payload", {})
    if slot in ("photo_triage", "photo_read"):
        print(json.dumps({"judgments": []}))
        return
    if slot == "assemble":
        print(json.dumps({"ok": True}))
        return
    if slot == "parse":
        prompt = (
            "Convert this venue ask into a structured request. Respond with ONLY a JSON "
            f"object shaped like {json.dumps(PARSE_SCHEMA)}. Decompose style analogies: put their "
            "observable, objectively checkable parts in required_attributes, put any purely subjective "
            "parts in preferences with effect_type ranking_signal, and never make the analogy itself a claim. "
            "Required attributes must be objectively "
            "checkable venue facts: products or drinks sold, services performed, physical features, "
            "or schedules. A photo, menu, official page, or Places field must be able to settle each "
            "one yes or no. Subjective character, mood, style, price feel, and crowd words (including "
            "upscale, quirky, cosy, romantic, lively, hip, authentic, not touristy, and hidden gem) "
            "are NEVER required attributes; put them in preferences with effect_type ranking_signal. "
            "Treat an either/or taste phrase such as upscale or quirky as one ranking signal, and never "
            "split an OR into several requirements. A concrete thing inside a taste phrase still counts: "
            "craft cocktail menu is checkable as product_inventory and stays required. Use the local "
            "language of the destination country. "
            "For every required attribute, include a synonyms list with English and destination-language wording. "
            "For a private in-room hot tub include jacuzzi, whirlpool, spa bath, hidromasaje, and bañera de hidromasaje. "
            "For hotel asks, fill stay from dates and guest count in the ask, and fill hotel_filters "
            "from explicit star range, guest score, pet-friendly, breakfast included, free cancellation, and "
            "hot tub, jacuzzi, whirlpool, spa bath, bañera de hidromasaje, or jacuzzi privado phrases. "
            "Put a named destination city in scope.city. arrival_start and arrival_end are the moment of "
            "arrival, not the length of the visit: set both to the same time unless the ask states a window. "
            "Do not invent scope coordinates. Preserve caller-supplied scope. If none is supplied and the ask names an anchor place, emit scope as kind=anchor with place, mode (walk by default), and max_min (10 by default). Always include "
            "ephemeral_card (it is ignored when a reviewed card exists).\n\nInput:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        print(json.dumps(ask_claude(prompt), ensure_ascii=False))
        return
    if slot == "judge":
        payload = compact_ledger(payload)
        prompt = (
            "You are an adversarial evidence judge. For each claim in the ledger, examine each "
            "evidence row whose claim_id matches. A claim's listed synonyms can satisfy it when the evidence "
            "ties the term to the requested subject. For a room-specific claim, a shared spa or property amenity "
            "does not qualify. Try to REFUTE the claim. Respond with ONLY a "
            'JSON object {"judgments": [{"claim_id": str, "evidence_id": str, "quote": str, '
            '"entails": bool, "contradicts": bool}]}. The quote MUST be copied verbatim from '
            "the evidence text (it is checked mechanically; a paraphrase is discarded). Quote the "
            "shortest passage that settles the claim, at most about 200 characters. Emit a "
            "judgment only when the evidence text actually addresses the claim, at most one per "
            "claim and evidence row, and nothing else.\n\nLedger:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        print(json.dumps(ask_claude(prompt), ensure_ascii=False))
        return
    print(json.dumps({}))

if __name__ == "__main__":
    main()
