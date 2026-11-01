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
from collections.abc import Callable
from dataclasses import dataclass

import torch
from primer_contracts.lab import HubModel, LabModelStatus, LocalModel, ModelSwitch

from primer_lab.config import Settings
from primer_lab.loader import (
    DeviceUnavailable,
    LoadedModel,
    LoadPlan,
    ModelFactory,
    load_model,
    plan_load,
)
from primer_lab.queue import RunQueue, RunTimedOut
from primer_lab.saes import Attachment, SaeSet, fetch_saes
from primer_lab.sources import Hub, ModelUnavailable, check_allowed, resolve

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
    saes: Attachment | None = None

    @property
    def ready_saes(self) -> SaeSet | None:
        return self.saes.ready if self.saes else None

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
    ) -> None:
        self._settings = settings
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
            if self.resident is not None and self.resident.saes is not None:
                await self._attach_saes(self.resident, self.resident.saes)

    async def _attach_saes(self, resident: Resident, attachment: Attachment) -> None:
        """Fetch the resident's SAEs beside whatever runs, and say how it went.

        Part of the switch's task, so waiting for a switch waits for these
        too, but after the switch is reported done: the model answers runs
        while the SAEs download. A failure here leaves the model as it is.
        """
        config = resident.loaded.model.config
        try:
            saes = await asyncio.to_thread(
                fetch_saes,
                self._settings,
                attachment.status.repository,
                self._hub,
                hidden_size=config.hidden_size,
                num_layers=resident.loaded.num_layers,
            )
        except ModelUnavailable as error:
            attachment.failed(str(error))
        except Exception:
            logger.exception("attaching SAEs failed")
            attachment.failed("The SAEs passed Lab's checks but could not be opened.")
        else:
            attachment.attached(saes)

    def _saes_for(self, reference: HubModel | LocalModel) -> Attachment | None:
        # By repository alone: an operator maps a model to the SAEs trained
        # for it, and a revision of the same model is still that model.
        if isinstance(reference, LocalModel):
            return None
        repository = self._settings.lab_saes.get(reference.repository)
        return Attachment(repository) if repository else None

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
        # Its SAEs are kept: they are memory-mapped files, not the weights.
        old = self.resident
        restore = (old.reference, old.loaded.plan, old.saes) if old else None
        old = self.resident = None
        _release()
        try:
            loaded = load_model(plan, self._factory)
            self.resident = Resident(reference, loaded, self._saes_for(reference))
        except BaseException:
            _release()
            if restore is not None:
                reference, plan, saes = restore
                self.resident = Resident(reference, load_model(plan, self._factory), saes)
            raise

    def _failed(self, reference: HubModel | LocalModel, message: str) -> None:
        self.switch = ModelSwitch(reference=reference, state="failed", error=message)


def _release() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
