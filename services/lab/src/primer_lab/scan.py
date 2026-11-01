"""Scanning a library's passages for ones that read like injected instructions.

Lab is handed text and never a library. The web app lists a library's
passages through Chat, which asks Control whether the person may read it,
and sends them here a batch at a time; so Lab holds no way to read a library
and nothing here decides who may. A batch is one turn of the run queue, so
a scan of a large library shares the model with everyone else between
batches, and stopping one is not sending the next.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from primer_contracts.indexing import Passage
from primer_contracts.lab import LabModelStatus, Probe
from pydantic import BaseModel, ConfigDict, Field

from primer_lab.identity import CurrentPrincipal
from primer_lab.inspection import special_ids, tokenize
from primer_lab.probes import owned_probe, probability
from primer_lab.routes import current_resident, run_queued
from primer_lab.scoring import Detectors, score_from

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])

#: The most passages a saved scan keeps. A scan is kept for what it found,
#: and what it found is at the top.
MAX_SCAN_FINDINGS = 50

#: Passages scored in one turn of the run queue. Small enough that a scan
#: does not hold the model for long between other people's runs.
MAX_SCAN_BATCH = 16


class ScanPassage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The caller's name for it, returned beside its scores. Lab reads
    #: nothing into it.
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=20_000)


class LabScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passages: list[ScanPassage] = Field(min_length=1, max_length=MAX_SCAN_BATCH)
    #: Read each passage with this probe of the caller's, too.
    probe_id: str | None = Field(default=None, max_length=64)


class ScannedPassage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    #: How many of its tokens were scored. Zero when the passage is a
    #: single token, which has nothing before it to be predicted from.
    tokens: int
    #: Only its first tokens were read: it was longer than this Lab reads.
    truncated: bool
    #: Mean surprisal, in bits per token: how unexpected the passage was as
    #: a whole. None when nothing was scored.
    mean_bits: float | None
    #: The most surprising single token, in bits.
    peak_bits: float | None
    #: The probe's probability at the passage's last scored token, which is
    #: where probes are trained to read. None without a probe.
    probe: float | None


class LabScanned(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: LabModelStatus
    probe: Probe | None = None
    #: In the order they were sent.
    passages: list[ScannedPassage]


class ScanSettings(BaseModel):
    """What a saved scan was of, as the web app asked for it.

    The library is recorded so the saved scan can say what it was of and
    link back; Lab never reads it. Opening the passages again goes through
    the libraries API, which asks again whether the person may.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    library_id: UUID
    library_name: str = Field(min_length=1, max_length=200)
    probe_id: str | None = Field(default=None, max_length=64)


class ScanFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passage: Passage
    scored: ScannedPassage


class ScanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: LabModelStatus
    probe: Probe | None = None
    #: Passages scored, of `total` in the library; fewer when the scan was
    #: stopped or the library is larger than one listing.
    scanned: int = Field(ge=0)
    total: int = Field(ge=0)
    #: The most anomalous, most anomalous first.
    findings: list[ScanFinding] = Field(max_length=MAX_SCAN_FINDINGS)


@router.post(
    "/scan",
    summary="Score a batch of passages for surprise and with a probe",
    description=(
        "Scores each passage as plain text: its mean and peak surprisal, and optionally "
        "what one of your probes makes of it at its last token. A passage longer than this "
        "Lab reads is scored from its start and marked truncated. Lab is given text, not a "
        "library: list a library's passages through the libraries API, then send them here."
    ),
)
async def scan(request: Request, body: LabScanRequest, principal: CurrentPrincipal) -> LabScanned:
    resident = current_resident(request)
    limit: int = request.app.state.settings.lab_max_prompt_tokens
    model = resident.loaded.model
    special = special_ids(model.tokenizer)
    detectors = Detectors(
        outlier_layer=None,
        probe=owned_probe(resident, principal.user_id, body.probe_id) if body.probe_id else None,
    )
    # Truncated rather than refused, unlike a prompt: one long passage
    # should not stop the rest of a library being scanned.
    encoded = [tokenize(model.tokenizer, p.text, chat=False).ids for p in body.passages]

    def work() -> list[ScannedPassage]:
        scanned: list[ScannedPassage] = []
        for passage, ids in zip(body.passages, encoded, strict=True):
            scored = score_from(model, ids[:limit], 1, special, detectors.layers)
            if scored is None:
                scanned.append(
                    ScannedPassage(
                        id=passage.id,
                        tokens=0,
                        truncated=len(ids) > limit,
                        mean_bits=None,
                        peak_bits=None,
                        probe=None,
                    )
                )
                continue
            bits = [token.surprisal_bits for token in scored.score.tokens]
            reading = None
            if detectors.probe is not None:
                summary, fitted = detectors.probe
                last = scored.residuals[summary.layer][-1:].double().numpy()
                [reading] = probability(fitted, last)
            scanned.append(
                ScannedPassage(
                    id=passage.id,
                    tokens=len(bits),
                    truncated=len(ids) > limit,
                    mean_bits=sum(bits) / len(bits),
                    peak_bits=max(bits),
                    probe=reading,
                )
            )
        return scanned

    passages = await run_queued(request, resident, work)
    return LabScanned(
        model=resident.status(),
        probe=detectors.probe[0] if detectors.probe else None,
        passages=passages,
    )
