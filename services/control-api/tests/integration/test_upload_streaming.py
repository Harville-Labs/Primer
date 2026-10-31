"""Uploads reach the source store as they arrive, written once.

Kept apart from `test_uploads.py` because these need a store that admits
more than a megabyte, which is where a spooled copy would start landing on
disk, while that file's quota is sized to be exceeded by a short literal.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from control_support import UserClient
from httpx2 import AsyncClient
from primer_storage import SourceStore

#: The subject the `owner` fixture signs in as, for requests it cannot shape.
OWNER = "oidc-owner"

#: Past Starlette's one-megabyte spool, so a spooled copy would be on disk.
LARGE = b"evidence\n" * (300 * 1024)


@pytest.fixture
def source_store(source_root: Path) -> SourceStore:
    return SourceStore(f"file://{source_root}", max_bytes=8 * 1024 * 1024)


@pytest.fixture
def temporary_files(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every temporary file opened during the test.

    A spooled file that outgrows memory calls `tempfile.TemporaryFile` to
    roll over, so recording that call sees the second copy whether or not
    the file is still listed in a directory - on POSIX it never is.
    """
    opened: list[Any] = []
    original = tempfile.TemporaryFile

    def recording(*args: Any, **kwargs: Any) -> Any:
        opened.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(tempfile, "TemporaryFile", recording)
    return opened


async def test_a_large_upload_is_not_copied_to_a_temporary_file_first(
    owner: UserClient, library_id: str, temporary_files: list[Any]
) -> None:
    """The bytes go to the store once, not to scratch disk and then the store.

    Parsing the form before the endpoint ran wrote every upload over a
    megabyte to the pod's local disk, so the copy cost as much disk and
    time as the file, and the quota could not stop it early.
    """
    response = await owner.upload(library_id, "large.txt", LARGE)

    assert response.status_code == 201
    assert response.json()["byte_size"] == len(LARGE)
    assert temporary_files == []


async def test_the_stored_bytes_are_the_uploaded_bytes(owner: UserClient, library_id: str) -> None:
    """Reassembling the parser's pieces must not drop or repeat any."""
    document = (await owner.upload(library_id, "large.txt", LARGE)).json()
    content = await owner.get(f"/api/v1/libraries/{library_id}/documents/{document['id']}/content")
    assert content.content == LARGE


async def test_a_body_that_is_not_a_form_is_refused(client: AsyncClient, library_id: str) -> None:
    response = await client.post(
        f"/api/v1/libraries/{library_id}/documents",
        content=b"evidence",
        headers={"X-Forwarded-User": OWNER, "Content-Type": "text/plain"},
    )
    assert response.status_code == 422
    assert "multipart/form-data" in response.json()["detail"]


async def test_a_form_without_a_file_part_is_refused(client: AsyncClient, library_id: str) -> None:
    response = await client.post(
        f"/api/v1/libraries/{library_id}/documents",
        files={"attachment": ("notes.txt", b"evidence", "text/plain")},
        headers={"X-Forwarded-User": OWNER},
    )
    assert response.status_code == 422
    assert "'file'" in response.json()["detail"]


async def test_a_body_cut_off_mid_file_stores_nothing(
    client: AsyncClient, library_id: str, source_root: Path
) -> None:
    """A dropped connection must not leave a truncated source behind."""
    body = b'--edge\r\nContent-Disposition: form-data; name="file"; filename="cut.txt"\r\n\r\nevid'
    response = await client.post(
        f"/api/v1/libraries/{library_id}/documents",
        content=body,
        headers={
            "X-Forwarded-User": OWNER,
            "Content-Type": "multipart/form-data; boundary=edge",
        },
    )
    assert response.status_code == 422
    assert not (source_root / "sources").exists()
