"""How long a saved experiment is kept, and that it goes when that ends."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from lab_support import as_user
from primer_lab.saved import purge_expired
from primer_service.db import Database, as_sync_url
from primer_service.identity import user_id_for_subject
from sqlalchemy import create_engine, text

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
MODEL = {
    "reference": {"source": "local", "path": "tiny"},
    "architecture": "qwen3",
    "device": "cpu",
    "dtype": "float32",
    "num_layers": 2,
}
RUN = {
    "kind": "neurons",
    "request": {"prompt": "hello", "chat": False, "max_new_tokens": 1},
}


def plant(url: str, owner: uuid.UUID, title: str, saved_at: datetime) -> uuid.UUID:
    """An experiment saved at a chosen time, written past the API to set its age."""
    identifier = uuid.uuid4()
    engine = create_engine(as_sync_url(url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO lab.experiments "
                    "(id, owner_user_id, kind, title, model, run, vectors, probes, created_at) "
                    "VALUES (:id, :owner, 'neurons', :title, CAST(:model AS jsonb), "
                    "CAST(:run AS jsonb), '[]', '[]', :at)"
                ),
                {
                    "id": identifier,
                    "owner": owner,
                    "title": title,
                    "model": json.dumps(MODEL),
                    "run": json.dumps(RUN),
                    "at": saved_at,
                },
            )
    finally:
        engine.dispose()
    return identifier


def titles(url: str) -> set[str]:
    engine = create_engine(as_sync_url(url))
    try:
        with engine.connect() as connection:
            return set(connection.execute(text("SELECT title FROM lab.experiments")).scalars())
    finally:
        engine.dispose()


def purge(url: str, now: datetime, days: int) -> int:
    async def run() -> int:
        database = Database(url)
        try:
            return await purge_expired(database, now=now, retention_days=days)
        finally:
            await database.dispose()

    return asyncio.run(run())


def test_the_cleanup_removes_expired_experiments_and_keeps_current_ones(empty_tables):
    owner = uuid.uuid4()
    plant(empty_tables, owner, "expired", NOW - timedelta(days=91))
    plant(empty_tables, owner, "just expired", NOW - timedelta(days=90, seconds=1))
    plant(empty_tables, owner, "current", NOW - timedelta(days=89))
    plant(empty_tables, uuid.uuid4(), "someone else's, current", NOW - timedelta(days=1))

    removed = purge(empty_tables, NOW, 90)

    assert removed == 2
    assert titles(empty_tables) == {"current", "someone else's, current"}


def test_the_retention_period_is_the_operators(empty_tables):
    plant(empty_tables, uuid.uuid4(), "a week old", NOW - timedelta(days=7))

    assert purge(empty_tables, NOW, 30) == 0
    assert purge(empty_tables, NOW, 5) == 1
    assert titles(empty_tables) == set()


def test_an_expired_experiment_is_hidden_before_the_cleanup_reaches_it(saving, empty_tables):
    """Retention is kept by every read, not only by however often the cleanup runs."""
    ada = user_id_for_subject("ada")
    now = datetime.now(UTC)
    plant(empty_tables, ada, "expired", now - timedelta(days=91))
    current = plant(empty_tables, ada, "current", now - timedelta(days=1))

    listed = saving.get("/api/v1/lab/experiments", headers=as_user("ada")).json()

    assert [entry["title"] for entry in listed] == ["current"]
    assert listed[0]["id"] == str(current)
    created = datetime.fromisoformat(listed[0]["created_at"])
    assert datetime.fromisoformat(listed[0]["expires_at"]) == created + timedelta(days=90)
    assert titles(empty_tables) == {"expired", "current"}, "reads delete nothing"
