from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from control_credentials import FORWARDED, WEB_TOKEN
from fastapi.testclient import TestClient
from primer_control.app import create_app
from primer_control.config import Settings

# Before any Settings is built: OIDC mode refuses to start without it.
os.environ["PRIMER_WEB_TOKEN"] = WEB_TOKEN


@pytest.fixture
def disabled_client() -> Iterator[TestClient]:
    settings = Settings(auth_mode="disabled")
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def oidc_client() -> Iterator[TestClient]:
    settings = Settings(auth_mode="oidc")
    with TestClient(create_app(settings), headers=FORWARDED) as client:
        yield client
