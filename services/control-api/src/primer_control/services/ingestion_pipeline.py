"""The stage machine every ingestion job walks.

Control is the only writer of job state, so this table is the single
definition of what may follow what. Workers ask to enter a stage and are
told whether they may; they never compute the next state themselves.

Each stage names three states. `entry` is what the job must be in to be
claimable, `active` is what a holder sets while working, and `done` is what
completion advances to - which is the next stage's `entry`. Duplicate
delivery is caught by that chain: a redelivered message finds the job past
its stage's entry state and is told the stage is already completed.
"""

from __future__ import annotations

from dataclasses import dataclass

from primer_contracts.documents import IngestionStatus
from primer_contracts.ingestion import StageName

#: States no transition may leave. A job reaching one of these is finished,
#: whether it succeeded or not.
TERMINAL_STATES = frozenset(
    {
        IngestionStatus.READY,
        IngestionStatus.FAILED,
        IngestionStatus.UNSUPPORTED,
        IngestionStatus.CANCELLED,
        IngestionStatus.DELETED,
    }
)

#: States meaning the job's work was abandoned rather than finished. A worker
#: holding a message for one of these stops without reporting failure.
ABANDONED_STATES = frozenset({IngestionStatus.CANCELLED, IngestionStatus.DELETED})


@dataclass(frozen=True)
class Stage:
    """One claimable unit of work and the states around it."""

    name: StageName
    entry: IngestionStatus
    active: IngestionStatus
    done: IngestionStatus

    @property
    def claimable_from(self) -> frozenset[IngestionStatus]:
        """States a claim may start from.

        `active` is included so an expired lease can be re-claimed: a worker
        that died mid-stage left the job marked active, and refusing to
        re-enter would strand it forever. The lease, not the state, is what
        stops a second live worker from claiming alongside the first.
        """
        return frozenset({self.entry, self.active})


#: Parse covers chunking too: Docling converts and chunks in one pass, so
#: splitting them would buy a queue hop and no isolation. `chunking` is the
#: state a parsed job rests in while it waits for an embedding worker.
STAGES: dict[StageName, Stage] = {
    StageName.PARSE: Stage(
        StageName.PARSE,
        entry=IngestionStatus.QUEUED,
        active=IngestionStatus.PARSING,
        done=IngestionStatus.CHUNKING,
    ),
    StageName.EMBED: Stage(
        StageName.EMBED,
        entry=IngestionStatus.CHUNKING,
        active=IngestionStatus.EMBEDDING,
        done=IngestionStatus.INDEXING,
    ),
    #: Indexing has no separate pending marker: writing an embedded batch
    #: into the store is the same step as waiting to. The lease still
    #: distinguishes a live holder from a redelivery.
    StageName.INDEX: Stage(
        StageName.INDEX,
        entry=IngestionStatus.INDEXING,
        active=IngestionStatus.INDEXING,
        done=IngestionStatus.READY,
    ),
    StageName.DELETE: Stage(
        StageName.DELETE,
        entry=IngestionStatus.DELETING,
        active=IngestionStatus.DELETING,
        done=IngestionStatus.DELETED,
    ),
}


def stage_for(name: StageName) -> Stage:
    return STAGES[name]


#: The stage a job waits for, by the state it rests in between stages. Every
#: message a worker receives is sent by Control for one of these, whether a
#: request or a completed stage put the job there.
WAITING_FOR: dict[IngestionStatus, StageName] = {
    stage.entry: stage.name for stage in STAGES.values()
}


def stage_waiting(status: IngestionStatus) -> StageName | None:
    """The stage a job in `status` needs a message for, if any."""
    return WAITING_FOR.get(status)
