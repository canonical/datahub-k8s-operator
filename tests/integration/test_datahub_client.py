#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the datahub-client relation."""

import functools
import logging
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict

import helpers
import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)

LIST_SERVICE_ACCOUNTS = textwrap.dedent("""\
    query listServiceAccounts($input: ListServiceAccountsInput!) {
        listServiceAccounts(input: $input) {
            total
            serviceAccounts {
                urn
                displayName
            }
        }
    }""")

# One service account per relation, named after the app and the relation ID.
MANAGED_NAME_PREFIX = f"[juju] {helpers.MCP_NAME}-"

NO_DATAHUB_MESSAGE = "missing required relation(s): datahub-client"


@pytest.fixture(scope="module")
def client_stack(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: Path, rock_resources: dict) -> jubilant.Juju:
    """Deploy DataHub with its full dependency stack, related to the MCP server."""
    try:
        k8s_juju.model_config({"update-status-hook-interval": "60s"})
    except jubilant.CLIError as exc:
        logger.warning("Could not set update-status-hook-interval: %s", exc)

    logger.info("Deploying '%s'", helpers.APP_NAME)
    helpers.deploy_charm(k8s_juju, charm, rock_resources)

    logger.info("Deploying LXD dependencies")
    helpers.deploy_lxd_dependencies(lxd_juju)

    logger.info("Consuming offers and integrating DataHub")
    helpers.consume_and_integrate(k8s_juju, lxd_juju)

    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME], timeout=30 * 60)

    logger.info("Deploying '%s' as a consumer", helpers.MCP_NAME)
    k8s_juju.deploy(helpers.MCP_NAME, channel=helpers.MCP_CHANNEL)
    # Nothing provides the relation yet, so blocked on it is the settled state.
    helpers.wait_for_apps_status(k8s_juju, {helpers.MCP_NAME: "blocked"}, timeout=15 * 60)

    logger.info("Integrating DataHub with '%s'", helpers.MCP_NAME)
    k8s_juju.integrate(f"{helpers.APP_NAME}:datahub-client", f"{helpers.MCP_NAME}:datahub-client")
    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME, helpers.MCP_NAME], timeout=15 * 60)

    return k8s_juju


@pytest.fixture(name="graphql")
def graphql_fixture(client_stack: jubilant.Juju) -> Callable[..., Dict[str, Any]]:
    """Return a callable running GraphQL queries as the DataHub admin."""
    session, url = helpers.datahub_graphql_session(client_stack)
    return functools.partial(helpers.graphql_query, session, url)


def _connection_databag(juju: jubilant.Juju) -> Dict[str, str]:
    """Return the application databag DataHub publishes on the datahub-client relation.

    Args:
        juju: Jubilant object.

    Returns:
        The published application data, empty when nothing is published yet.
    """
    raw = juju.cli("show-unit", f"{helpers.APP_NAME}/0", "--format=yaml", include_model=True)
    unit_data = yaml.safe_load(raw)[f"{helpers.APP_NAME}/0"]
    for relation in unit_data.get("relation-info", []):
        if relation.get("endpoint") == "datahub-client":
            return relation.get("application-data", {})
    return {}


def _managed_accounts(graphql: Callable[..., Dict[str, Any]]) -> Dict[str, str]:
    """Return the service accounts created for datahub-client relations, URN to name.

    Args:
        graphql: Callable running GraphQL queries.

    Returns:
        Mapping of service account URN to display name.
    """
    data = graphql(LIST_SERVICE_ACCOUNTS, {"input": {"start": 0, "count": 100}})
    accounts = data["listServiceAccounts"]["serviceAccounts"]
    return {
        account["urn"]: account["displayName"]
        for account in accounts
        if (account.get("displayName") or "").startswith(MANAGED_NAME_PREFIX)
    }


def test_relation_publishes_a_connection(client_stack: jubilant.Juju):
    """The provider publishes a GMS URL, a token secret ID, and the service account URN."""
    databag = _connection_databag(client_stack)
    logger.info("DATAHUB_CLIENT_DATABAG %s", {key: databag.get(key) for key in databag})

    gms_url = databag.get("gms-url", "")
    assert gms_url, f"no GMS URL published: {databag}"
    # No gms-ingress relation is in play here, so the URL is the in-cluster one.
    assert gms_url.startswith("http"), f"GMS URL is not a URL: {gms_url}"

    assert databag.get("secret-id", "").startswith("secret"), f"no token secret published: {databag}"
    assert databag.get("service-account-urn", "").startswith(
        "urn:li:corpuser:"
    ), f"no service account URN published: {databag}"


def test_relation_creates_one_service_account(client_stack: jubilant.Juju, graphql):
    """The relation gets a DataHub service account of its own, named after it."""
    accounts = _managed_accounts(graphql)
    logger.info("DATAHUB_CLIENT_SERVICE_ACCOUNTS %s", accounts)

    assert len(accounts) == 1, f"expected exactly one service account for the relation, found {accounts}"

    urn, name = next(iter(accounts.items()))
    assert name.removeprefix(MANAGED_NAME_PREFIX).isdigit(), f"service account is not named after a relation: {name}"
    assert _connection_databag(client_stack)["service-account-urn"] == urn


def test_consumer_serves_on_the_relation_credentials(client_stack: jubilant.Juju):
    """The MCP server reaches active with nothing configured but the relation."""
    status = client_stack.status()
    unit_status = status.apps[helpers.MCP_NAME].units[f"{helpers.MCP_NAME}/0"].workload_status
    assert unit_status.current == "active", f"consumer did not settle active: {unit_status.message}"


def test_relation_broken_deletes_the_service_account(client_stack: jubilant.Juju, graphql):
    """Removing the relation takes the service account away with it."""
    juju = client_stack

    logger.info("Removing the datahub-client relation")
    juju.remove_relation(f"{helpers.APP_NAME}:datahub-client", f"{helpers.MCP_NAME}:datahub-client")
    helpers.wait_for_apps_status(juju, {helpers.MCP_NAME: "blocked"}, timeout=15 * 60)

    unit_status = juju.status().apps[helpers.MCP_NAME].units[f"{helpers.MCP_NAME}/0"].workload_status
    assert NO_DATAHUB_MESSAGE in (unit_status.message or "")

    helpers.poll_until(
        juju,
        lambda: not _managed_accounts(graphql),
        "the service account outlived the relation it belonged to",
    )


def test_reintegration_issues_new_credentials(client_stack: jubilant.Juju, graphql):
    """Re-adding the relation provisions a fresh service account and token."""
    juju = client_stack

    logger.info("Re-adding the datahub-client relation")
    juju.integrate(f"{helpers.APP_NAME}:datahub-client", f"{helpers.MCP_NAME}:datahub-client")
    helpers.wait_for_all_active(juju, [helpers.APP_NAME, helpers.MCP_NAME], timeout=15 * 60)

    helpers.poll_until(
        juju,
        lambda: len(_managed_accounts(graphql)) == 1,
        "no service account was provisioned for the re-added relation",
    )
    assert _connection_databag(juju)["service-account-urn"] in _managed_accounts(graphql)
