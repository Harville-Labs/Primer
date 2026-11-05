"""What a broker failure looks like by the time it leaves the publisher."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from kombu.exceptions import OperationalError
from primer_contracts.ingestion import StageName
from primer_control.publisher import BrokerUnreachable, CeleryJobPublisher, broker_host

BROKER_URL = "amqp://primer:hunter2@primer-rabbitmq.apps.svc.cluster.local:5672//"


def unreachable(*_args: Any, **_kwargs: Any) -> None:
    raise OperationalError("[Errno -2] Name or service not known")


def test_an_unreachable_broker_is_named_by_host_without_its_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = CeleryJobPublisher(BROKER_URL)
    monkeypatch.setattr(publisher._app, "send_task", unreachable)

    with pytest.raises(BrokerUnreachable) as raised:
        publisher.publish(StageName.PARSE, uuid4())

    message = str(raised.value)
    assert message.startswith(
        "the ingestion broker at primer-rabbitmq.apps.svc.cluster.local:5672 is unreachable"
    )
    assert "Name or service not known" in message
    assert "hunter2" not in message
    assert "primer:" not in message


def test_the_translated_error_drops_kombus_misleading_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kombu raises from inside an AttributeError handler, and that is what logs led with."""
    publisher = CeleryJobPublisher(BROKER_URL)
    monkeypatch.setattr(publisher._app, "send_task", unreachable)

    with pytest.raises(BrokerUnreachable) as raised:
        publisher.publish(StageName.PARSE, uuid4())

    assert raised.value.__suppress_context__


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("amqp://user:p%40ss@rabbit:5672//", "rabbit:5672"),
        ("amqp://user:p@ss@rabbit//", "rabbit"),
        ("redis://cache.local/0", "cache.local"),
    ],
)
def test_the_broker_host_never_carries_a_password(url: str, host: str) -> None:
    assert broker_host(url) == host
