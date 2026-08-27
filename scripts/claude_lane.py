#!/usr/bin/env python3
"""Smoke-test model lane: routes osusume slots through the local claude CLI.

Reads {"slot": ..., "payload": ...} on stdin, prints one JSON object.
Photo slots return empty judgments (text lane cannot inspect images), so
photo-dependent claims fail closed to unknown instead of being invented.
"""
import json
import re
import subprocess
import sys

MODEL = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5"

PARSE_SCHEMA = {
    "request": {
        "ask": "str", "category": "one lowercase token, e.g. ceramics/restaurant/hotel",
        "country": "ISO2", "local_language": "ISO2",
        "required_attributes": [{"claim_id": "snake_case", "text": "observable attribute", "claim_type": "one of: product_inventory,counter_service,layout,quality,prices,event_schedule,generic", "required": True, "_rules": "never add an opening-hours attribute (the arrival window covers hours); event_schedule is ONLY for recurring events like markets/fairs"}],
        "scope": {}, "arrival_start": "RFC3339 or null", "arrival_end": "RFC3339 or null",
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
    out = subprocess.run(
        ["claude", "-p", "--model", MODEL, "--output-format", "json"],
        input=prompt, capture_output=True, text=True, timeout=240,
    )
    if out.returncode != 0:
        raise SystemExit(f"claude lane failed: {out.stderr[:300]}")
    text = json.loads(out.stdout)["result"]
    match = re.search(r"\{.*\}", text, re.S)
    return json.loads(match.group(0) if match else text)

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
            f"object shaped like {json.dumps(PARSE_SCHEMA)}. Decompose style analogies into "
            "observable attributes. Use the local language of the destination country. "
            "Do not invent scope coordinates; leave scope as given. Always include "
            "ephemeral_card (it is ignored when a reviewed card exists).\n\nInput:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        print(json.dumps(ask_claude(prompt), ensure_ascii=False))
        return
    if slot == "judge":
        prompt = (
            "You are an adversarial evidence judge. For each claim in the ledger, examine each "
            "evidence row whose claim_id matches. Try to REFUTE the claim. Respond with ONLY a "
            'JSON object {"judgments": [{"claim_id": str, "evidence_id": str, "quote": str, '
            '"entails": bool, "contradicts": bool}]}. The quote MUST be copied verbatim from '
            "the evidence text (it is checked mechanically; a paraphrase is discarded). Emit a "
            "judgment only when the evidence text actually addresses the claim.\n\nLedger:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        print(json.dumps(ask_claude(prompt), ensure_ascii=False))
        return
    print(json.dumps({}))

if __name__ == "__main__":
    main()
