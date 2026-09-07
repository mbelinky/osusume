from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def page_fingerprint(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _room_name_from_text(text: str) -> str:
    compact = " ".join(str(text).split())
    prefix = re.match(r"^([^:]{2,80}):\s*\S", compact)
    if prefix:
        return prefix.group(1).strip()
    named = re.search(
        r"\b(?:the|el|la|le|il|lo|l')?\s*([A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ' -]{0,55}(?:Penthouse|Suite|Room|Habitaci[oó]n|Habitaci[oó]))\b",
        compact,
    )
    if named:
        return " ".join(named.group(1).split())
    penthouse = re.search(r"\b(Penthouse(?:\s+[A-ZÀ-ÖØ-Þ][\wÀ-ÖØ-öø-ÿ'-]*)?)\b", compact, re.I)
    return " ".join(penthouse.group(1).split()) if penthouse else ""


def passages_from_pages(pages: Iterable[dict[str, Any]], language: str = "") -> list[dict[str, str]]:
    passages: list[dict[str, str]] = []
    for page in pages:
        page_url = str(page.get("url") or "")
        page_language = str(page.get("language") or language or "")
        rows = page.get("room_passages") or []
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = " ".join(str(row.get("text") or "").split())
            if not text:
                continue
            passages.append(
                {
                    "room_name": " ".join(str(row.get("room_name") or _room_name_from_text(text)).split()),
                    "text": text,
                    "language": str(row.get("language") or page_language),
                    "page_url": str(row.get("page_url") or row.get("url") or page_url),
                    "retrieved_at": str(row.get("retrieved_at") or page.get("retrieved_at") or ""),
                    "source_kind": str(row.get("source_kind") or page.get("source_kind") or "official"),
                    "identity_label": str(row.get("identity_label") or page.get("identity_label") or ""),
                }
            )
        if rows:
            continue
        text = " ".join(str(page.get("text") or "").split())
        room_name = _room_name_from_text(text)
        if room_name and text:
            passages.append(
                {
                    "room_name": room_name,
                    "text": text,
                    "language": page_language,
                    "page_url": page_url,
                    "retrieved_at": str(page.get("retrieved_at") or ""),
                    "source_kind": str(page.get("source_kind") or "official"),
                    "identity_label": str(page.get("identity_label") or ""),
                }
            )
    return passages


@dataclass(frozen=True)
class IndexLookup:
    status: str
    pages: tuple[dict[str, Any], ...] = ()
    room_passages: tuple[dict[str, str], ...] = ()

    @property
    def from_index(self) -> bool:
        return self.status in {"fresh", "failure"}


class RoomIndex:
    """Persistent official-page and room-passage cache, keyed by Places id."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS official_pages (
                    place_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    page_json TEXT NOT NULL,
                    PRIMARY KEY (place_id, url)
                );
                CREATE TABLE IF NOT EXISTS room_passages (
                    place_id TEXT NOT NULL,
                    page_url TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    room_name TEXT NOT NULL,
                    passage_text TEXT NOT NULL,
                    language TEXT NOT NULL,
                    passage_json TEXT NOT NULL,
                    PRIMARY KEY (place_id, page_url, position),
                    FOREIGN KEY (place_id, page_url)
                        REFERENCES official_pages(place_id, url) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS fetch_failures (
                    place_id TEXT PRIMARY KEY,
                    failed_at TEXT NOT NULL
                );
                """
            )

    def lookup(
        self,
        place_id: str,
        *,
        now: datetime | None = None,
        max_age_days: float = 30,
        failure_ttl_hours: float = 12,
    ) -> IndexLookup:
        current = _utc(now)
        with self._connect() as connection:
            failure = connection.execute(
                "SELECT failed_at FROM fetch_failures WHERE place_id = ?", (place_id,)
            ).fetchone()
            if failure and current - _parse_time(failure["failed_at"]) <= timedelta(hours=failure_ttl_hours):
                return IndexLookup("failure")
            rows = connection.execute(
                "SELECT page_json, fetched_at FROM official_pages WHERE place_id = ? ORDER BY url", (place_id,)
            ).fetchall()
            if not rows:
                return IndexLookup("miss")
            newest = max(_parse_time(row["fetched_at"]) for row in rows)
            if current - newest > timedelta(days=max_age_days):
                return IndexLookup("stale")
            pages = tuple(json.loads(row["page_json"]) for row in rows)
            passage_rows = connection.execute(
                "SELECT passage_json FROM room_passages WHERE place_id = ? ORDER BY page_url, position", (place_id,)
            ).fetchall()
            passages = tuple(json.loads(row["passage_json"]) for row in passage_rows)
            return IndexLookup("fresh", pages, passages)

    def replace(
        self,
        place_id: str,
        pages: Iterable[dict[str, Any]],
        *,
        now: datetime | None = None,
        language: str = "",
    ) -> None:
        fetched_at = _utc(now).isoformat()
        page_rows = []
        page_urls = set()
        for page in pages:
            url = str(page.get("url") or "")
            if not url or url in page_urls:
                continue
            page_urls.add(url)
            page_rows.append(dict(page))
        with self._connect() as connection:
            connection.execute("DELETE FROM room_passages WHERE place_id = ?", (place_id,))
            connection.execute("DELETE FROM official_pages WHERE place_id = ?", (place_id,))
            for page in page_rows:
                page_time = str(page.get("retrieved_at") or fetched_at)
                page["retrieved_at"] = page_time
                page_passages = passages_from_pages([page], language)
                connection.execute(
                    "INSERT OR REPLACE INTO official_pages(place_id, url, fingerprint, fetched_at, page_json) VALUES (?, ?, ?, ?, ?)",
                    (
                        place_id,
                        str(page["url"]),
                        page_fingerprint(str(page.get("text") or "")),
                        fetched_at,
                        json.dumps(page, ensure_ascii=False, sort_keys=True),
                    ),
                )
                for position, passage in enumerate(page_passages):
                    connection.execute(
                        """INSERT INTO room_passages(
                               place_id, page_url, position, room_name, passage_text, language, passage_json
                           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            place_id,
                            str(page["url"]),
                            position,
                            passage["room_name"],
                            passage["text"],
                            passage["language"],
                            json.dumps(passage, ensure_ascii=False, sort_keys=True),
                        ),
                    )
            connection.execute("DELETE FROM fetch_failures WHERE place_id = ?", (place_id,))

    def record_failure(self, place_id: str, *, now: datetime | None = None) -> None:
        failed_at = _utc(now).isoformat()
        with self._connect() as connection:
            connection.execute("DELETE FROM room_passages WHERE place_id = ?", (place_id,))
            connection.execute("DELETE FROM official_pages WHERE place_id = ?", (place_id,))
            connection.execute(
                "INSERT INTO fetch_failures(place_id, failed_at) VALUES (?, ?) "
                "ON CONFLICT(place_id) DO UPDATE SET failed_at = excluded.failed_at",
                (place_id, failed_at),
            )

    # Short aliases make the storage object convenient in focused callers and tests.
    get = lookup
    store = replace
