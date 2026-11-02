"""The single place Studio authorization is decided.

Studio images are private to the person who made or uploaded them, and
nobody else: not a colleague a library is shared with, and not an
administrator. An administrator sets limits and sees how much each person
uses (`StudioUsage`), and nothing about that needs the pictures or prompts.

Predicates, as `LibraryAccess` has, so that sharing an image later changes
this object and nothing that lists or fetches one.
"""

from __future__ import annotations

from uuid import UUID

from primer_control.models import StudioImage
from sqlalchemy import ColumnElement


class StudioAccess:
    def readable(self, principal_id: UUID) -> ColumnElement[bool]:
        return StudioImage.owner_user_id == principal_id

    def manageable(self, principal_id: UUID) -> ColumnElement[bool]:
        return StudioImage.owner_user_id == principal_id
