"""Policy checks on the authenticating proxy that every request passes through."""

from __future__ import annotations

from test_chart import containers, named, render


def test_the_proxy_waits_as_long_as_the_web_app_does_for_a_response() -> None:
    """oauth2-proxy gives up on its upstream after 30 seconds by default.

    Everything reaches the web app through it, and the web app waits up to
    five minutes for Lab or Control to begin answering. A Lab run on a CPU
    routinely takes longer than 30 seconds, so with the default the proxy
    answered 502 while Lab went on to finish a run nobody was waiting for.
    """
    args = containers(named(render(), "Deployment", "-auth"))[0]["args"]

    assert "--upstream-timeout=5m" in args
