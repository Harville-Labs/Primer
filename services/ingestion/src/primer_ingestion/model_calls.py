"""How a call to an operator's model fails, for every stage that makes one.

Ingestion calls models Primer does not run: one to extract a graph, one to
describe a picture. Each fails the same few ways, and each failure has to
reach Control as a code that says which model, and whether trying again can
help, rather than as a transport error nobody reading the job can act on.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager

import openai

from primer_ingestion.errors import PermanentStageError, StageError

logger = logging.getLogger(__name__)


@contextmanager
def model_failures(code: str, name: str, settings: str) -> Generator[None]:
    """Report a model's failures as that model's.

    `code` prefixes the stage codes (`<code>_unavailable`, `<code>_rejected`),
    `name` is how a person would say which model, and `settings` the prefix
    of the variables that configure it.

    A refusal on the request's merits - a wrong model name, a bad key - is
    refused identically on every retry, so it is permanent. Everything else
    is retried: what the client raises for a refused connection, a timeout
    or an overloaded server is not a set worth enumerating, and the retry
    budget bounds the cost of being wrong.
    """
    try:
        yield
    except openai.APIStatusError as error:
        if error.status_code >= 500 or error.status_code in (408, 409, 429):
            raise StageError(
                f"{code}_unavailable", f"The {name} answered {error.status_code}."
            ) from error
        raise PermanentStageError(
            f"{code}_rejected",
            f"The {name} refused the request ({error.status_code}). "
            f"Check {settings}_MODEL and {settings}_API_KEY.",
        ) from error
    except Exception as error:
        logger.warning("the %s could not be reached", name, exc_info=True)
        raise StageError(f"{code}_unavailable", f"The {name} could not be reached.") from error
