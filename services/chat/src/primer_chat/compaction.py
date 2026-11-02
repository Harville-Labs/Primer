"""Remembering the turns that no longer fit.

Dropping the oldest turns keeps a conversation inside the window, but it
also means a thread quietly loses its own beginning: the document someone
named twenty messages ago, the constraint they gave once, the thing they
already said they had tried. What is dropped is not noise, it is just old.

So the turns that fall out are summarized instead, and the summary is
carried in their place. It is incremental - each pass folds the newly
dropped turns into what was already remembered - because re-reading the
whole conversation every time would grow with the conversation, which is the
opposite of what compacting is for.

Two things are deliberate. The summary is written by the same endpoint that
answers, which means compaction costs a model call, so it happens only when
something is actually about to be dropped. And a failure here is never a
failed turn: the fallback is the behaviour that existed before, which is to
drop the turns and answer anyway. A conversation losing its memory is worse
than the alternative only if the alternative is answering; it is much better
than an error.
"""

from __future__ import annotations

import logging

from primer_chat.budget import truncate_to_tokens
from primer_chat.config import Settings
from primer_chat.generation import ChatGenerator
from primer_chat.rag import SUMMARY_SYSTEM_PROMPT, HistoryTurn, build_summary_prompt
from primer_chat.reasoning import Channel, Fragment

logger = logging.getLogger(__name__)


class Compactor:
    """Folds the turns that fell out of the window into a running summary."""

    def __init__(self, settings: Settings, generator: ChatGenerator) -> None:
        self._settings = settings
        self._generator = generator

    async def compact(
        self,
        previous: str | None,
        dropped: tuple[HistoryTurn, ...],
        *,
        budget: int,
        model: str | None = None,
    ) -> str | None:
        """The summary that replaces `previous` and `dropped` together.

        None when there was nothing to do, or when the model could not be
        asked. The caller carries on either way: this is memory, not the
        answer.
        """
        if not dropped:
            return None

        prompt = build_summary_prompt(previous, dropped)
        try:
            # The answer channel only. A reasoning model's deliberation is
            # not its summary, and folding it in would put scratch work into
            # the memory every later turn is built on.
            written = "".join(
                [
                    fragment.text
                    async for fragment in self._generator.stream(
                        SUMMARY_SYSTEM_PROMPT, prompt, model=model
                    )
                    # No tools are offered here, so no call can come back;
                    # the check is what the generator's type promises.
                    if isinstance(fragment, Fragment) and fragment.channel is Channel.ANSWER
                ]
            ).strip()
        except Exception:
            # Logged rather than raised. The turn this was called during is
            # already under way, and a user waiting for an answer should not
            # be handed an error because bookkeeping failed.
            logger.exception("compaction failed; the dropped turns are forgotten")
            return None

        if not written:
            return None

        # A model asked to be brief is not obliged to be. The room reserved
        # for the summary was subtracted from the history's budget, so it is
        # enforced here rather than assumed.
        return truncate_to_tokens(
            written,
            budget=budget,
            characters_per_token=self._settings.chat_characters_per_token,
        )
