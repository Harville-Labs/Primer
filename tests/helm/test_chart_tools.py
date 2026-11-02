"""Tools in the rendered chart: what Chat is told, and the search server it may run.

Kept apart from `test_chart.py` because tools are optional, and the
assertions that matter are about who can reach the search server and what
Chat is offered.
"""

from __future__ import annotations

import json
from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render

TOOLS_ON = ("tools.enabled=true",)
SEARCH_ON = (*TOOLS_ON, "tools.search.enabled=true")


def tools_of(rendered: list[dict[str, Any]]) -> dict[str, Any]:
    return json.loads(env_of(named(rendered, "Deployment", "-chat"))["PRIMER_TOOLS"])


def test_with_tools_off_chat_is_told_nothing_and_no_search_runs() -> None:
    """Off is the default, and must render as it did before tools existed."""
    rendered = render()

    assert "PRIMER_TOOLS" not in env_of(named(rendered, "Deployment", "-chat"))
    assert not [doc for doc in rendered if doc["metadata"]["name"].endswith("-search")]
    assert "search-secret" not in named(rendered, "Secret", "-internal")["data"]


def test_the_operators_servers_reach_chat_as_written() -> None:
    """Passed through in Chat's own field names, so Chat validates them, not the chart."""
    rendered = render(
        *TOOLS_ON,
        "tools.servers[0].name=docs",
        "tools.servers[0].url=http://docs-mcp:8080/mcp",
        "tools.servers[0].allowed_tools[0]=search_docs",
    )

    assert tools_of(rendered) == {
        "enabled": True,
        "approval_ttl_seconds": 300,
        "servers": [
            {"name": "docs", "url": "http://docs-mcp:8080/mcp", "allowed_tools": ["search_docs"]}
        ],
    }


def test_the_bundled_search_server_is_offered_to_chat_at_its_service() -> None:
    rendered = render(*SEARCH_ON)
    service = named(rendered, "Service", "-search")

    servers = tools_of(rendered)["servers"]
    assert servers == [
        {
            "name": "search",
            "url": f"http://{service['metadata']['name']}:3000/mcp",
            "allowed_tools": ["searxng_web_search", "web_url_read"],
            "required_groups": [],
        }
    ]


def test_only_chat_may_reach_the_search_server() -> None:
    """It has no authentication of its own, and it is a way out to the internet.

    SearXNG is not given a port in the policy at all: it listens on
    localhost for the MCP server beside it, and nothing else needs it.
    """
    rendered = render(*SEARCH_ON)
    policy = named(rendered, "NetworkPolicy", "-allow-search")

    (rule,) = policy["spec"]["ingress"]
    assert rule["ports"] == [{"port": 3000, "protocol": "TCP"}]
    assert [
        peer["podSelector"]["matchLabels"]["app.kubernetes.io/component"] for peer in rule["from"]
    ] == ["chat"]


def test_searxng_listens_only_inside_its_pod() -> None:
    rendered = render(*SEARCH_ON)
    deployment = named(rendered, "Deployment", "-search")
    by_name = {container["name"]: container for container in containers(deployment)}

    searxng = {entry["name"]: entry for entry in by_name["searxng"]["env"]}
    assert searxng["GRANIAN_HOST"]["value"] == "127.0.0.1"
    assert "ports" not in by_name["searxng"]
    assert [port["containerPort"] for port in by_name["mcp"]["ports"]] == [3000]


def test_the_search_server_never_reads_cluster_addresses() -> None:
    """web_url_read fetches what the model names. A cluster address must not be one."""
    rendered = render(*SEARCH_ON)
    mcp = next(
        c for c in containers(named(rendered, "Deployment", "-search")) if c["name"] == "mcp"
    )

    env = {entry["name"]: entry.get("value") for entry in mcp["env"]}
    assert env["MCP_HTTP_ALLOW_PRIVATE_URLS"] == "false"


def test_searxngs_secret_is_the_releases_own() -> None:
    rendered = render(*SEARCH_ON)
    internal = named(rendered, "Secret", "-internal")
    searxng = next(
        c for c in containers(named(rendered, "Deployment", "-search")) if c["name"] == "searxng"
    )

    assert "search-secret" in internal["data"]
    secret = {entry["name"]: entry for entry in searxng["env"]}["SEARXNG_SECRET"]
    assert secret["valueFrom"]["secretKeyRef"] == {
        "name": internal["metadata"]["name"],
        "key": "search-secret",
    }


def test_the_search_server_has_its_own_account() -> None:
    rendered = render(*SEARCH_ON)
    account = named(rendered, "ServiceAccount", "-search")
    deployment = named(rendered, "Deployment", "-search")

    assert (
        deployment["spec"]["template"]["spec"]["serviceAccountName"] == account["metadata"]["name"]
    )
    assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


def test_search_without_tools_is_refused() -> None:
    """A search server Chat would never call is configuration that only looks like it works."""
    assert "tools.enabled is false" in refusal("tools.search.enabled=true")


def test_a_server_named_like_the_bundled_one_is_refused() -> None:
    """Chat keys servers by name; two called `search` would leave one unreachable."""
    message = refusal(
        *SEARCH_ON,
        "tools.servers[0].name=search",
        "tools.servers[0].url=http://elsewhere/mcp",
    )

    assert "Rename yours" in message


def test_every_container_in_the_search_pod_runs_unprivileged() -> None:
    rendered = render(*SEARCH_ON)
    found = list(containers(named(rendered, "Deployment", "-search")))
    assert found, "the search pod has no containers, so this test would check nothing"

    for container in found:
        context = container["securityContext"]
        assert context["readOnlyRootFilesystem"] is True, container["name"]
        assert context["allowPrivilegeEscalation"] is False, container["name"]
    assert of_kind(rendered, "ConfigMap"), "SearXNG's settings were not rendered"
