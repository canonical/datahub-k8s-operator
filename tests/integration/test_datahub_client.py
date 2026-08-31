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

RELATION_ENDPOINT = "datahub-client"

# The three fields the provider publishes. A connection is usable only with all of them.
CONNECTION_FIELDS = ("gms-url", "secret-id", "service-account-urn")

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
    k8s_juju.integrate(f"{helpers.APP_NAME}:{RELATION_ENDPOINT}", f"{helpers.MCP_NAME}:{RELATION_ENDPOINT}")
    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME, helpers.MCP_NAME], timeout=15 * 60)

    return k8s_juju


@pytest.fixture(name="graphql")
def graphql_fixture(client_stack: jubilant.Juju) -> Callable[..., Dict[str, Any]]:
    """Return a callable running GraphQL queries as the DataHub admin."""
    session, url = helpers.datahub_graphql_session(client_stack)
    return functools.partial(helpers.graphql_query, session, url)


def _published_connection(juju: jubilant.Juju) -> Dict[str, str]:
    """Return the application databag DataHub publishes on the relation.

    Args:
        juju: Jubilant object.

    Returns:
        The published application data, empty when nothing is published yet.
    """
    raw = juju.cli("show-unit", f"{helpers.MCP_NAME}/0", "--format=yaml")
    unit_data = yaml.safe_load(raw)[f"{helpers.MCP_NAME}/0"]
    for relation in unit_data.get("relation-info", []):
        if relation.get("endpoint") == RELATION_ENDPOINT:
            return relation.get("application-data", {})
    return {}


def _wait_for_connection(juju: jubilant.Juju) -> Dict[str, str]:
    """Poll until DataHub has published every connection field, then return them.

    Args:
        juju: Jubilant object.

    Returns:
        The published application data.

    Raises:
        AssertionError: If the connection is still incomplete at the timeout.
    """
    connection: Dict[str, str] = {}

    def _published() -> bool:
        """Return True once every connection field carries a value."""
        nonlocal connection
        connection = _published_connection(juju)
        return all(connection.get(field) for field in CONNECTION_FIELDS)

    try:
        helpers.poll_until(juju, _published, "connection never published")
    except AssertionError as exc:
        raise AssertionError(f"DataHub never published a complete connection. Last seen: {connection}") from exc
    return connection


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


def _wait_for_managed_accounts(
    juju: jubilant.Juju,
    graphql: Callable[..., Dict[str, Any]],
    predicate: Callable[[Dict[str, str]], bool],
    message: str,
    timeout: float = 15 * 60,
) -> Dict[str, str]:
    """Poll the Juju-managed service accounts until they satisfy a predicate.

    Args:
        juju: Jubilant object.
        graphql: Callable running GraphQL queries.
        predicate: Called with the managed accounts keyed by URN.
        message: Assertion message used if the predicate never holds.
        timeout: Maximum seconds to wait.

    Returns:
        The managed accounts, as read when the predicate held.

    Raises:
        AssertionError: If the predicate does not hold before the timeout.
    """
    accounts: Dict[str, str] = {}

    def _ready() -> bool:
        """Re-read the managed accounts and evaluate the predicate."""
        nonlocal accounts
        accounts = _managed_accounts(graphql)
        return predicate(accounts)

    try:
        helpers.poll_until(juju, _ready, message, timeout=timeout)
    except AssertionError as exc:
        raise AssertionError(f"{message}. Last seen: {accounts}") from exc
    return accounts


def _matches_the_published_account(juju: jubilant.Juju) -> Callable[[Dict[str, str]], bool]:
    """Return a predicate that holds when DataHub owns exactly the account it published.

    Args:
        juju: Jubilant object.

    Returns:
        A predicate over the managed accounts keyed by URN.
    """

    def _matches(found: Dict[str, str]) -> bool:
        """Compare the managed accounts against the currently published URN."""
        published = _published_connection(juju).get("service-account-urn")
        return bool(published) and set(found) == {published}

    return _matches


def test_relation_publishes_a_connection(client_stack: jubilant.Juju):
    """The provider publishes a GMS URL, a token secret ID, and the service account URN."""
    connection = _wait_for_connection(client_stack)
    logger.info("DATAHUB_CLIENT_DATABAG %s", sorted(connection))

    # No gms-ingress relation is in play here, so the URL is the in-cluster one.
    assert connection["gms-url"].startswith("http"), f"GMS URL is not a URL: {connection['gms-url']}"
    assert connection["secret-id"].startswith("secret"), f"not a secret ID: {connection['secret-id']}"
    assert connection["service-account-urn"].startswith(
        "urn:li:corpuser:"
    ), f"malformed service account URN: {connection['service-account-urn']}"


def test_relation_creates_one_service_account(client_stack: jubilant.Juju, graphql):
    """The relation gets a DataHub service account of its own, named after it."""
    juju = client_stack

    accounts = _wait_for_managed_accounts(
        juju,
        graphql,
        _matches_the_published_account(juju),
        "DataHub never converged on exactly the service account it published",
    )
    logger.info("DATAHUB_CLIENT_SERVICE_ACCOUNTS %s", accounts)

    name = next(iter(accounts.values()))
    assert name.removeprefix(MANAGED_NAME_PREFIX).isdigit(), f"service account is not named after a relation: {name}"


def test_consumer_serves_on_the_relation_credentials(client_stack: jubilant.Juju):
    """The MCP server reaches active with nothing configured but the relation."""
    status = client_stack.status()
    unit_status = status.apps[helpers.MCP_NAME].units[f"{helpers.MCP_NAME}/0"].workload_status
    assert unit_status.current == "active", f"consumer did not settle active: {unit_status.message}"


def test_relation_broken_deletes_the_service_account(client_stack: jubilant.Juju, graphql):
    """Removing the relation takes the service account away with it."""
    juju = client_stack

    logger.info("Removing the datahub-client relation")
    juju.remove_relation(f"{helpers.APP_NAME}:{RELATION_ENDPOINT}", f"{helpers.MCP_NAME}:{RELATION_ENDPOINT}")
    helpers.wait_for_apps_status(juju, {helpers.MCP_NAME: "blocked"}, timeout=15 * 60)

    unit_status = juju.status().apps[helpers.MCP_NAME].units[f"{helpers.MCP_NAME}/0"].workload_status
    assert NO_DATAHUB_MESSAGE in (unit_status.message or "")

    _wait_for_managed_accounts(
        juju,
        graphql,
        lambda found: not found,
        "the service account outlived the relation it belonged to",
    )

    def _relation_is_gone(status: jubilant.Status) -> bool:
        """Return True once the consumer no longer lists the relation."""
        app = status.apps.get(helpers.MCP_NAME)
        return app is not None and not app.relations.get(RELATION_ENDPOINT)

    juju.wait(_relation_is_gone, timeout=10 * 60, delay=10)


def test_reintegration_issues_new_credentials(client_stack: jubilant.Juju, graphql):
    """Re-adding the relation provisions a fresh service account and token."""
    juju = client_stack

    logger.info("Re-adding the datahub-client relation")
    juju.integrate(f"{helpers.APP_NAME}:{RELATION_ENDPOINT}", f"{helpers.MCP_NAME}:{RELATION_ENDPOINT}")
    helpers.wait_for_all_active(juju, [helpers.APP_NAME, helpers.MCP_NAME], timeout=15 * 60)

    _wait_for_managed_accounts(
        juju,
        graphql,
        _matches_the_published_account(juju),
        "the re-added relation did not get a service account of its own",
    )
