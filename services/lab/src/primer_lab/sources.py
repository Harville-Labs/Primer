"""Turn the model a user named into a directory Lab can load.

This is the only module in Lab that reaches the network, and it reaches
only the hub. Everything here runs before a byte of weights is read into
the process, so a refusal costs a request to the hub at most.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import (
    GatedRepoError,
    HfHubHTTPError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)
from huggingface_hub.utils import filter_repo_objects
from primer_contracts.lab import HubModel, LocalModel

from primer_lab.config import Settings
from primer_lab.weights import WeightsRefused, check_config, check_weights

logger = logging.getLogger(__name__)

#: Every file a fetch writes. Weights, configuration, and tokenizer
#: vocabularies - all of them data. A repository's `.bin` checkpoints and
#: `.py` modelling code are never downloaded, so they are never on disk for
#: a later mistake to load.
FETCHED_PATTERNS = (
    "*.safetensors",
    "*.json",
    "tokenizer.model",
    "merges.txt",
    "vocab.txt",
    "*.tiktoken",
)

#: Under the weights volume. A user naming a local path cannot reach it,
#: so the cache's layout is never something a request can address.
HUB_CACHE = "hub"


class ModelUnavailable(RuntimeError):
    """The named model cannot be used, said so its owner can act on it."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class Listing:
    """What the hub says a revision holds, before anything is downloaded."""

    commit: str
    #: Path in the repository to its size in bytes, None where unreported.
    files: dict[str, int | None]


class Hub(Protocol):
    """The two things Lab asks of the hub. Faked in tests."""

    def describe(self, repository: str, revision: str | None) -> Listing: ...

    def download(self, repository: str, commit: str, patterns: list[str]) -> Path: ...


class HuggingFaceHub:
    def __init__(self, settings: Settings) -> None:
        self._endpoint = settings.lab_hub_endpoint
        self._cache = settings.lab_weights_dir / HUB_CACHE
        self._api = HfApi(endpoint=self._endpoint)

    def describe(self, repository: str, revision: str | None) -> Listing:
        info = self._api.model_info(repository, revision=revision, files_metadata=True)
        if info.sha is None:
            raise ModelUnavailable(f"The hub did not say which commit {repository} is at.")
        return Listing(
            commit=info.sha,
            files={sibling.rfilename: sibling.size for sibling in info.siblings or ()},
        )

    def download(self, repository: str, commit: str, patterns: list[str]) -> Path:
        # By commit, never by branch: the size check above was of this
        # commit, and a branch can move between the two calls.
        path = snapshot_download(
            repository,
            revision=commit,
            allow_patterns=patterns,
            cache_dir=self._cache,
            endpoint=self._endpoint,
        )
        return Path(str(path))


@dataclass(frozen=True, slots=True)
class Resolved:
    directory: Path
    architecture: str
    commit: str | None


def in_volume(settings: Settings, relative: str | Path) -> Path | None:
    """Where `relative` leads in the weights volume, or None if it leads out.

    Resolved before the comparison, so `..` and a symlink pointing out of
    the volume are both caught: what is checked is where the path actually
    leads, not how it was spelled. The volume itself is not inside it.
    """
    root = settings.lab_weights_dir.resolve()
    target = (root / relative).resolve()
    return target if target != root and target.is_relative_to(root) else None


def local_directory(settings: Settings, model: LocalModel) -> Resolved:
    """The operator's directory, confined to the weights volume."""
    directory = in_volume(settings, model.path)
    if directory is None:
        raise ModelUnavailable(f"{model.path!r} is not a directory inside the weights volume.")
    if directory.is_relative_to(settings.lab_weights_dir.resolve() / HUB_CACHE):
        raise ModelUnavailable(
            f"{model.path!r} is inside Lab's download cache. Name the repository instead."
        )
    try:
        return Resolved(directory=directory, architecture=check_weights(directory), commit=None)
    except WeightsRefused as error:
        raise ModelUnavailable(str(error)) from None


def check_allowed(settings: Settings, model: HubModel | LocalModel) -> None:
    """Refuse a repository the operator's patterns exclude.

    Cheap enough to run on the request that asked, so a user hears about it
    at once rather than from a switch that failed in the background.
    """
    if isinstance(model, LocalModel):
        return
    patterns = settings.lab_repository_patterns
    if not any(fnmatch.fnmatchcase(model.repository, pattern) for pattern in patterns):
        raise ModelUnavailable(
            f"This deployment does not allow {model.repository}. It allows repositories "
            f"matching {', '.join(patterns) or 'nothing'}."
        )


def resolve(settings: Settings, model: HubModel | LocalModel, hub: Hub) -> Resolved:
    if isinstance(model, LocalModel):
        return local_directory(settings, model)
    return fetch(settings, model, hub)


def fetch(settings: Settings, model: HubModel, hub: Hub) -> Resolved:
    """Check a repository against everything that can refuse it, then fetch it.

    In order of cost: the operator's patterns need nothing; the file list
    is one request; `config.json` is one small file. Only a repository that
    passes all three has its weights downloaded.
    """
    repository = model.repository
    check_allowed(settings, model)
    with hub_errors(repository, model.revision):
        listing = hub.describe(repository, model.revision)
        wanted = list(filter_repo_objects(listing.files, allow_patterns=list(FETCHED_PATTERNS)))
        _check_listing(settings, repository, listing, wanted)
        check_config(hub.download(repository, listing.commit, ["config.json"]) / "config.json")
        directory = hub.download(repository, listing.commit, list(FETCHED_PATTERNS))
        architecture = check_weights(directory)
    return Resolved(directory=directory, architecture=architecture, commit=listing.commit)


@contextmanager
def hub_errors(repository: str, revision: str | None) -> Generator[None]:
    """Translate what the hub and the volume raise into a ModelUnavailable.

    Shared by everything Lab fetches, so a model and the SAEs for it fail
    in the same words.
    """
    try:
        yield
    except WeightsRefused as error:
        raise ModelUnavailable(f"{repository}: {error}") from None
    except GatedRepoError:
        raise ModelUnavailable(
            f"{repository} is gated: its owner requires accepting terms before downloading. "
            "Lab fetches without an account, so choose an ungated copy of it."
        ) from None
    except RepositoryNotFoundError:
        raise ModelUnavailable(f"There is no public repository called {repository}.") from None
    except RevisionNotFoundError:
        raise ModelUnavailable(f"{repository} has no revision {revision!r}.") from None
    except (HfHubHTTPError, OSError) as error:
        # The network, the hub, or the volume - not the user's choice. Logged
        # with the cause; told to the user as worth trying again.
        logger.warning("fetching %s failed: %s", repository, error)
        raise ModelUnavailable(
            f"Lab could not fetch {repository} from the model hub. Try again shortly.",
            retryable=True,
        ) from error


def check_size(repository: str, listing: Listing, wanted: list[str], *, limit: int) -> None:
    """Refuse a fetch the hub cannot size, or that is over the operator's `limit` in bytes."""
    sizes = [listing.files.get(name) for name in wanted]
    if any(size is None for size in sizes):
        raise ModelUnavailable(f"The hub did not report the size of every file in {repository}.")
    total = sum(size for size in sizes if size is not None)
    if total > limit:
        raise ModelUnavailable(
            f"{repository} is {total / 1e9:.1f} GB, over this deployment's limit of "
            f"{limit / 1e9:.1f} GB."
        )


def _check_listing(
    settings: Settings, repository: str, listing: Listing, wanted: list[str]
) -> None:
    weights = [name for name in wanted if name.endswith(".safetensors")]
    if not weights:
        raise ModelUnavailable(
            f"{repository} publishes no safetensors weights. Lab loads nothing else, because "
            "other checkpoint formats can run code when they are opened."
        )
    check_size(repository, listing, wanted, limit=settings.lab_max_model_bytes)
