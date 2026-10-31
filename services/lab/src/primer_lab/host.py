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

    def status(self) -> LabModelStatus:
        plan = self.loaded.plan
        return LabModelStatus(
            reference=self.reference,
            commit=plan.commit,
            architecture=plan.architecture,
            device=plan.device,
            dtype=plan.dtype,
            num_layers=self.loaded.num_layers,
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
        previous = (self.resident.reference, self.resident.loaded.plan) if self.resident else None
        self.resident = None
        _release()
        try:
            self.resident = Resident(reference, load_model(plan, self._factory))
        except BaseException:
            _release()
            if previous is not None:
                self.resident = Resident(previous[0], load_model(previous[1], self._factory))
            raise

    def _failed(self, reference: HubModel | LocalModel, message: str) -> None:
        self.switch = ModelSwitch(reference=reference, state="failed", error=message)


def _release() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
