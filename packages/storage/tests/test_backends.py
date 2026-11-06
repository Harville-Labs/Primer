"""The storage backends a deployment can actually be pointed at.

`fsspec` dispatches on the URL scheme and imports the backend lazily, so a
missing one is not a startup error or an import error in this package: it is
an `ImportError` raised the first time somebody uploads a file. Primer
checks the scheme where configuration is read instead, and these pin that
check: what it lets through, what it refuses, and what it says.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest
from primer_storage import SourceStore, UnsupportedStorage, check_url
from primer_storage.backends import PROBE_DIRECTORY, open_filesystem

#: Every scheme Primer's configuration and chart tell an operator to use.
#: `file` is the Compose default; `s3` is the chart's.
DOCUMENTED_SCHEMES = ["file:///var/lib/primer/sources", "s3://primer-sources"]


@pytest.mark.parametrize("url", DOCUMENTED_SCHEMES)
def test_a_documented_source_store_url_resolves(url: str) -> None:
    """No network is touched: resolving a URL only selects the backend."""
    filesystem, path = open_filesystem(url)

    assert filesystem is not None
    assert path


@pytest.mark.parametrize(
    "url",
    ["ftp://nas.local/primer", "smb://nas/primer", "memory://sources", "/var/lib/primer"],
)
def test_a_scheme_primer_does_not_support_is_refused_with_the_ones_it_does(url: str) -> None:
    """fsspec would accept every one of these. `memory://` would even work,
    until a restart lost every upload; a bare path is refused so the scheme
    is always stated rather than guessed."""
    with pytest.raises(UnsupportedStorage) as refused:
        check_url(url)

    message = str(refused.value)
    assert "PRIMER_SOURCE_STORE_URL" in message
    assert "`file://`" in message
    assert "`s3://`" in message


def test_a_store_cannot_be_built_on_an_unsupported_scheme() -> None:
    """The check is in the one place every store opens its filesystem, so a
    caller that skips configuration does not skip it."""
    with pytest.raises(UnsupportedStorage):
        SourceStore("ftp://nas.local/primer", max_bytes=1)


def test_an_s3_url_without_its_backend_installed_says_which_package_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args: None if name == "s3fs" else real(name, *args),
    )

    with pytest.raises(UnsupportedStorage, match="s3fs"):
        check_url("s3://primer-sources")


def store(root: Path) -> SourceStore:
    return SourceStore(f"file://{root}", max_bytes=1)


async def test_the_readiness_probe_writes_reads_and_leaves_nothing_behind(tmp_path: Path) -> None:
    assert await store(tmp_path).check() is True

    assert list((tmp_path / PROBE_DIRECTORY).iterdir()) == []


@pytest.fixture
def read_only_root(tmp_path: Path) -> Iterator[Path]:
    tmp_path.chmod(0o555)
    yield tmp_path
    tmp_path.chmod(0o755)


async def test_the_readiness_probe_fails_on_a_store_that_cannot_be_written(
    read_only_root: Path,
) -> None:
    """A read-only mount passes an existence check and fails every upload,
    which is the case the probe is for."""
    with pytest.raises(PermissionError):
        await store(read_only_root).check()
