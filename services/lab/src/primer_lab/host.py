"""The one resident model, and switching it for another.

A switch has two halves with different costs. Fetching can take minutes
and touches only the network and the disk, so it runs beside whatever runs
are in progress. Loading puts weights on the device, so it goes through the
run queue like a forward pass: it waits for the run ahead of it, and no run
starts while it is under way.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import torch
from primer_contracts.lab import (
    HubModel,
    LabModelStatus,
    LocalModel,
    ModelSwitch,
    Probe,
    SaeStatus,
    SteeringVector,
    TranscoderStatus,
)

from primer_lab.attachment import Attachment
from primer_lab.config import Settings
from primer_lab.labels import FeatureLabels
from primer_lab.loader import (
    DeviceUnavailable,
    LoadedModel,
    LoadPlan,
    ModelFactory,
    load_model,
    plan_load,
)
from primer_lab.owned import Owned
from primer_lab.queue import RunQueue, RunTimedOut
from primer_lab.reference import References
from primer_lab.saes import SaeSet, fetch_saes
from primer_lab.sources import Hub, ModelUnavailable, check_allowed, resolve
from primer_lab.transcoders import TranscoderSet, fetch_transcoders

if TYPE_CHECKING:
    # Probes are trained by routes that need the resident, so the import is one way at runtime.
    from primer_lab.probes import Fitted

logger = logging.getLogger(__name__)

#: Reading a few gigabytes from a cold disk onto a device. Far longer than a
#: run is allowed, because a load that is abandoned leaves no model at all.
LOAD_TIMEOUT_SECONDS = 1800.0


class SwitchInProgress(RuntimeError):
    def __init__(self, switch: ModelSwitch) -> None:
        super().__init__("A different model is already being fetched or loaded.")
        self.switch = switch


@dataclass(frozen=True, slots=True)
class Resident:
    reference: HubModel | LocalModel
    loaded: LoadedModel
    saes: Attachment[SaeSet, SaeStatus] | None = None
    transcoders: Attachment[TranscoderSet, TranscoderStatus] | None = None
    #: Made on this model and meaningless on any other, so they go with it.
    vectors: Owned[SteeringVector, torch.Tensor] = field(default_factory=Owned)
    probes: Owned[Probe, Fitted] = field(default_factory=Owned)
    references: References = field(default_factory=References)

    @property
    def ready_saes(self) -> SaeSet | None:
        return self.saes.ready if self.saes else None

    @property
    def ready_transcoders(self) -> TranscoderSet | None:
        return self.transcoders.ready if self.transcoders else None

    def status(self) -> LabModelStatus:
        plan = self.loaded.plan
        return LabModelStatus(
            reference=self.reference,
            commit=plan.commit,
            architecture=plan.architecture,
            device=plan.device,
            dtype=plan.dtype,
            num_layers=self.loaded.num_layers,
            saes=self.saes.status if self.saes else None,
            transcoders=self.transcoders.status if self.transcoders else None,
        )


class ModelHost:
    def __init__(
        self,
        settings: Settings,
        queue: RunQueue,
        hub: Hub,
        factory: ModelFactory,
        *,
        cuda_available: Callable[[], bool] | None = None,
        labels: Mapping[str, FeatureLabels] | None = None,
    ) -> None:
        self._settings = settings
        #: Read at startup, so a malformed file stops Lab there; matched to
        #: SAEs here, once they are fetched and their shape is known.
        self._labels = labels or {}
        self._queue = queue
        self._hub = hub
        self._factory = factory
        self._cuda_available = cuda_available
        self.resident: Resident | None = None
        self.switch: ModelSwitch | None = None
        self._task: asyncio.Task[None] | None = None

    def request(self, reference: HubModel | LocalModel) -> None:
        """Start switching to `reference`, unless that is already in hand.

        Returns once the switch has started; the status reports how it goes.
        One switch at a time, and a second is refused rather than queued:
        two people racing to load different models would otherwise each
        wait for a load whose result the other then threw away.
        """
        if self.switch is not None and self.switch.state != "failed":
            if self.switch.reference == reference:
                return
            raise SwitchInProgress(self.switch)
        if self.resident is not None and self.resident.reference == reference:
            self.switch = None
            return
        check_allowed(self._settings, reference)
        self.switch = ModelSwitch(reference=reference, state="fetching")
        self._task = asyncio.create_task(self._switch_to(reference))

    async def wait(self) -> None:
        """Until the switch in progress has finished. For tests and shutdown."""
        if self._task is not None:
            await self._task

    async def _switch_to(self, reference: HubModel | LocalModel) -> None:
        try:
            resolved = await asyncio.to_thread(resolve, self._settings, reference, self._hub)
            plan = plan_load(self._settings, resolved, cuda_available=self._cuda_available)
            self.switch = ModelSwitch(reference=reference, state="loading")
            await self._queue.run(
                lambda: self._replace(reference, plan), limit_seconds=LOAD_TIMEOUT_SECONDS
            )
        except (ModelUnavailable, DeviceUnavailable) as error:
            self._failed(reference, str(error))
        except RunTimedOut:
            self._failed(reference, "Loading the model took too long and was abandoned.")
        except Exception:
            # Past the checks, a failure is the loader's: most often an
            # architecture nnterp cannot map onto its standard names.
            logger.exception("loading a model failed")
            self._failed(
                reference,
                "The model passed Lab's checks but could not be loaded. Its architecture may "
                "not be one Lab can inspect yet.",
            )
        else:
            self.switch = None
            resident = self.resident
            if resident is not None and resident.saes is not None:
                await self._attach(
                    resident,
                    resident.saes,
                    fetch_saes,
                    lambda saes: saes.labelled(self._labels.get(saes.repository)),
                    "The SAEs passed Lab's checks but could not be opened.",
                )
            if resident is not None and resident.transcoders is not None:
                await self._attach(
                    resident,
                    resident.transcoders,
                    fetch_transcoders,
                    lambda transcoders: transcoders,
                    "The transcoders passed Lab's checks but could not be read.",
                )

    async def _attach[Ready: SaeSet | TranscoderSet, Status: (SaeStatus, TranscoderStatus)](
        self,
        resident: Resident,
        attachment: Attachment[Ready, Status],
        fetch: Callable[..., Ready],
        finish: Callable[[Ready], Ready],
        unexpected: str,
    ) -> None:
        """Fetch something for the resident beside whatever runs, and say how it went.

        Part of the switch's task, so waiting for a switch waits for these
        too, but after the switch is reported done: the model answers runs
        while they download. A failure here leaves the model as it is.
        """
        config = resident.loaded.model.config
        try:
            ready = await asyncio.to_thread(
                fetch,
                self._settings,
                attachment.status.repository,
                self._hub,
                hidden_size=config.hidden_size,
                num_layers=resident.loaded.num_layers,
            )
        except ModelUnavailable as error:
            attachment.failed(str(error))
        except Exception:
            logger.exception("attaching %s failed", attachment.status.repository)
            attachment.failed(unexpected)
        else:
            attachment.attached(finish(ready))

    # Both by repository alone: an operator maps a model to what was
    # trained for it, and a revision of the same model is still that model.

    def _saes_for(self, reference: HubModel | LocalModel) -> Attachment[SaeSet, SaeStatus] | None:
        if isinstance(reference, LocalModel):
            return None
        repository = self._settings.lab_saes.get(reference.repository)
        return Attachment(repository, SaeStatus) if repository else None

    def _transcoders_for(
        self, reference: HubModel | LocalModel
    ) -> Attachment[TranscoderSet, TranscoderStatus] | None:
        # Without a builder there is nothing to build a graph with, and no
        # reason to fetch 60 GB for it.
        if isinstance(reference, LocalModel) or self._settings.lab_graph_python is None:
            return None
        repository = self._settings.lab_transcoders.get(reference.repository)
        return Attachment(repository, TranscoderStatus) if repository else None

    def _replace(self, reference: HubModel | LocalModel, plan: LoadPlan) -> None:
        """Swap the resident model, on the queue's thread.

        The old model is released before the new one loads, because a GPU
        that can hold one model often cannot hold two. That means a failed
        load has nothing to fall back on unless it puts the old one back -
        which it does, so a bad choice by one user does not leave Lab
        without a model for everyone else.
        """
        # Only what is needed to load it again: holding the old Resident
        # here would hold its weights, and the release would free nothing.
        # Its SAEs and transcoders are kept: they are files, not the weights.
        old = self.resident
        restore = (old.reference, old.loaded.plan, old.saes, old.transcoders) if old else None
        old = self.resident = None
        _release()
        try:
            loaded = load_model(plan, self._factory)
            self.resident = Resident(
                reference, loaded, self._saes_for(reference), self._transcoders_for(reference)
            )
        except BaseException:
            _release()
            if restore is not None:
                reference, plan, saes, transcoders = restore
                self.resident = Resident(
                    reference, load_model(plan, self._factory), saes, transcoders
                )
            raise

    def _failed(self, reference: HubModel | LocalModel, message: str) -> None:
        self.switch = ModelSwitch(reference=reference, state="failed", error=message)


def _release() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
