#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the trino-catalog relation.

The relation only bootstraps ingestion sources: it creates one per catalog,
keeps their Trino connection details current, and never deletes anything. These
tests exercise that contract against a real Trino charm and a real GMS.
"""

import functools
import json
import logging
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict

import helpers
import jubilant
import pytest
import yaml

logger = logging.getLogger(__name__)

CATALOGS = ("sales", "marketing")
EXTRA_CATALOG = "finance"
EDITED_CATALOG = "sales"

STALE_HOST = "stale.example.com:8080"
STALE_USERNAME = "stale-user"
OPERATOR_SCHEMA_PATTERN = {"allow": ["^operator_only$"], "deny": []}
OPERATOR_PROFILING = {"enabled": True}
OPERATOR_SCHEDULE = {"interval": "15 4 * * *", "timezone": "Europe/Rome"}
OPERATOR_ENV_KEY = "OPERATOR_ENV"
OPERATOR_ENV_VALUE = "keep-me"

LIST_INGESTION_SOURCES = textwrap.dedent("""\
    query listIngestionSources($input: ListIngestionSourcesInput!) {
        listIngestionSources(input: $input) {
            total
            ingestionSources {
                urn
                name
                type
                config {
                    recipe
                    executorId
                    extraArgs {
                        key
                        value
                    }
                }
                schedule {
                    interval
                    timezone
                }
            }
        }
    }""")

UPDATE_INGESTION_SOURCE = textwrap.dedent("""\
    mutation updateIngestionSource($urn: String!, $input: UpdateIngestionSourceInput!) {
        updateIngestionSource(urn: $urn, input: $input)
    }""")

LIST_SECRETS = textwrap.dedent("""\
    query listSecrets($input: ListSecretsInput!) {
        listSecrets(input: $input) {
            total
            secrets {
                urn
                name
            }
        }
    }""")


@pytest.fixture(scope="module")
def trino_stack(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: Path, rock_resources: dict) -> jubilant.Juju:
    """Deploy DataHub with its full dependency stack, related to Trino."""
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

    logger.info("Deploying '%s' with catalogs %s", helpers.TRINO_NAME, ", ".join(CATALOGS))
    helpers.deploy_trino(k8s_juju, CATALOGS)

    logger.info("Integrating DataHub with Trino")
    k8s_juju.integrate(f"{helpers.APP_NAME}:trino-catalog", f"{helpers.TRINO_NAME}:trino-catalog")
    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME, helpers.TRINO_NAME], timeout=15 * 60)

    return k8s_juju


@pytest.fixture(name="graphql")
def graphql_fixture(trino_stack: jubilant.Juju) -> Callable[..., Dict[str, Any]]:
    """Return a callable running GraphQL queries as the DataHub admin."""
    session, url = helpers.datahub_graphql_session(trino_stack)
    return functools.partial(helpers.graphql_query, session, url)


def _catalog_of(name: str) -> str:
    """Return the catalog name a Juju-managed ingestion source is named after."""
    return name.removeprefix("[juju] ").removesuffix("-ingestion")


def _env_vars(source: Dict[str, Any]) -> Dict[str, str]:
    """Return the executor environment of an ingestion source."""
    for arg in source["config"]["extraArgs"] or []:
        if arg["key"] == "extra_env_vars":
            return json.loads(arg["value"])
    return {}


def _is_juju_managed(source: Dict[str, Any]) -> bool:
    """Return True if an ingestion source carries the Juju-managed marker."""
    return _env_vars(source).get("JUJU_MANAGED") == "true"


def _recipe(source: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the recipe of an ingestion source.

    DataHub stores recipes as JSON when the charm writes them and as YAML once
    the UI has saved them. JSON is valid YAML, so one parser reads both.
    """
    return yaml.safe_load(source["config"]["recipe"])


def _is_json(recipe: str) -> bool:
    """Return True if a stored recipe is JSON rather than YAML."""
    try:
        json.loads(recipe)
    except json.JSONDecodeError:
        return False
    return True


def _managed_sources(graphql: Callable[..., Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Return the Juju-managed ingestion sources, keyed by catalog name."""
    data = graphql(LIST_INGESTION_SOURCES, {"input": {"start": 0, "count": 100}})
    sources = data["listIngestionSources"]["ingestionSources"]
    return {_catalog_of(source["name"]): source for source in sources if _is_juju_managed(source)}


def _urns(sources: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Return the URN of each ingestion source, keyed by catalog name."""
    return {catalog: source["urn"] for catalog, source in sources.items()}


def _put_recipe(
    graphql: Callable[..., Dict[str, Any]],
    source: Dict[str, Any],
    recipe: Dict[str, Any],
    *,
    schedule: Dict[str, str] | None = None,
    env_vars: Dict[str, str] | None = None,
) -> None:
    """Save an edited recipe the way an operator editing it in the UI would.

    The UI submits YAML and ``updateIngestionSource`` replaces the whole source,
    so everything the source is made of is sent back along with the recipe.

    Args:
        graphql: Callable running GraphQL queries.
        source: The ingestion source being edited.
        recipe: The edited recipe.
        schedule: Optional replacement schedule.
        env_vars: Optional replacement executor environment.
    """
    extra_args = [dict(arg) for arg in source["config"]["extraArgs"] or []]
    if env_vars is not None:
        extra_args = [
            {"key": arg["key"], "value": json.dumps(env_vars) if arg["key"] == "extra_env_vars" else arg["value"]}
            for arg in extra_args
        ]

    input_data = {
        "name": source["name"],
        "type": source["type"],
        "schedule": schedule or dict(source["schedule"]),
        "config": {
            "recipe": yaml.safe_dump(recipe, sort_keys=False),
            "executorId": source["config"]["executorId"],
            "extraArgs": extra_args,
        },
    }
    graphql(UPDATE_INGESTION_SOURCE, {"urn": source["urn"], "input": input_data})


def _wait_for_sources(
    juju: jubilant.Juju,
    graphql: Callable[..., Dict[str, Any]],
    predicate: Callable[[Dict[str, Dict[str, Any]]], bool],
    message: str,
    timeout: float = 8 * 60,
) -> Dict[str, Dict[str, Any]]:
    """Poll the Juju-managed ingestion sources until they satisfy a predicate.

    Args:
        juju: Jubilant object, used for its polling loop.
        graphql: Callable running GraphQL queries.
        predicate: Called with the managed sources keyed by catalog name.
        message: Assertion message used if the predicate never holds.
        timeout: Maximum seconds to wait.

    Returns:
        The managed sources, as read when the predicate held.

    Raises:
        AssertionError: If the predicate does not hold before the timeout.
    """
    last_seen: Dict[str, Dict[str, Any]] = {}

    def _ready(_: jubilant.Status) -> bool:
        """Evaluate the predicate, treating a failed read as not ready."""
        nonlocal last_seen
        try:
            last_seen = _managed_sources(graphql)
            return predicate(last_seen)
        except Exception as exc:
            logger.info("Ingestion source check not ready yet: %s", exc)
            return False

    try:
        juju.wait(_ready, timeout=timeout, delay=15, successes=1)
    except TimeoutError as exc:
        raise AssertionError(f"{message}. Last seen: {last_seen}") from exc
    return last_seen


def _prove_reconciled(juju: jubilant.Juju, graphql: Callable[..., Dict[str, Any]], catalog: str) -> None:
    """Wait for a reconciliation to happen, by making one leave a visible mark.

    The host is a field the charm owns, so pointing it somewhere stale and
    waiting for it to come back is a positive signal that a reconciliation has
    run to completion, which asserting an absence of changes cannot give.

    Args:
        juju: Jubilant object.
        graphql: Callable running GraphQL queries.
        catalog: Catalog whose ingestion source is used as the marker.
    """
    source = _managed_sources(graphql)[catalog]
    recipe = _recipe(source)
    expected_host = helpers.trino_relation_url(juju)
    recipe["source"]["config"]["host_port"] = STALE_HOST
    _put_recipe(graphql, source, recipe)

    _wait_for_sources(
        juju,
        graphql,
        lambda sources: _recipe(sources[catalog])["source"]["config"]["host_port"] == expected_host,
        f"the charm never refreshed the host of the '{catalog}' ingestion source",
    )


def test_relation_creates_an_ingestion_per_catalog(trino_stack: jubilant.Juju, graphql):
    """Every related catalog gets a Juju-managed ingestion source and a password secret."""
    juju = trino_stack

    sources = _wait_for_sources(
        juju,
        graphql,
        lambda found: set(CATALOGS) <= set(found),
        "ingestion sources were never created for the related catalogs",
    )

    expected_host = helpers.trino_relation_url(juju)
    for catalog in CATALOGS:
        source = sources[catalog]
        assert source["name"] == f"[juju] {catalog}-ingestion"
        assert source["type"] == "trino"
        assert source["schedule"]["interval"], "ingestion source was created without a schedule"

        recipe = _recipe(source)
        assert recipe["source"]["type"] == "trino"
        config = recipe["source"]["config"]
        assert config["host_port"] == expected_host
        assert config["database"] == catalog
        assert config["username"].startswith(f"app-{helpers.APP_NAME}-")
        # Trino only accepts a password over HTTPS, and the relation advertises
        # the plain HTTP service URL, so the recipe is written without one.
        assert "password" not in config

    logger.info("Verifying the DataHub secrets backing the ingestion sources")
    data = graphql(LIST_SECRETS, {"input": {"start": 0, "count": 100}})
    secret_names = {secret["name"] for secret in data["listSecrets"]["secrets"]}
    assert "JUJU_MANAGED_GMS_TOKEN" in secret_names
    for catalog in CATALOGS:
        assert f"JUJU_MANAGED_TRINO_PASSWORD_{catalog.upper()}" in secret_names


def test_operator_edits_survive_reconciliation(trino_stack: jubilant.Juju, graphql):
    """Reconciliation refreshes the connection fields and leaves operator edits alone."""
    juju = trino_stack

    source = _managed_sources(graphql)[EDITED_CATALOG]
    expected_host = helpers.trino_relation_url(juju)
    expected_username = _recipe(source)["source"]["config"]["username"]

    logger.info("Editing the '%s' ingestion source as an operator would", EDITED_CATALOG)
    recipe = _recipe(source)
    config = recipe["source"]["config"]
    config["host_port"] = STALE_HOST
    config["username"] = STALE_USERNAME
    config["schema_pattern"] = OPERATOR_SCHEMA_PATTERN
    config["profiling"] = OPERATOR_PROFILING
    env_vars = {**_env_vars(source), OPERATOR_ENV_KEY: OPERATOR_ENV_VALUE}
    _put_recipe(graphql, source, recipe, schedule=OPERATOR_SCHEDULE, env_vars=env_vars)

    logger.info("Waiting for the charm to refresh the connection fields")
    sources = _wait_for_sources(
        juju,
        graphql,
        lambda found: _recipe(found[EDITED_CATALOG])["source"]["config"]["host_port"] == expected_host,
        "the charm never refreshed the edited ingestion source",
    )

    refreshed = sources[EDITED_CATALOG]
    config = _recipe(refreshed)["source"]["config"]
    assert config["host_port"] == expected_host
    assert config["username"] == expected_username
    assert config["database"] == EDITED_CATALOG

    logger.info("Verifying the operator's own edits were left in place")
    assert config["schema_pattern"] == OPERATOR_SCHEMA_PATTERN
    assert config["profiling"] == OPERATOR_PROFILING
    assert refreshed["schedule"]["interval"] == OPERATOR_SCHEDULE["interval"]
    assert refreshed["schedule"]["timezone"] == OPERATOR_SCHEDULE["timezone"]
    assert _env_vars(refreshed)[OPERATOR_ENV_KEY] == OPERATOR_ENV_VALUE
    assert _env_vars(refreshed)["JUJU_MANAGED"] == "true"

    # A recipe saved from the UI is YAML, and the charm has to patch it without
    # turning it back into the JSON it originally wrote.
    assert not _is_json(refreshed["config"]["recipe"]), "the charm rewrote an operator's YAML recipe as JSON"


def test_new_catalog_is_added_and_removed_catalog_is_kept(trino_stack: jubilant.Juju, graphql):
    """A new catalog gets an ingestion source; removing it again keeps the source."""
    juju = trino_stack

    logger.info("Adding catalog '%s' to Trino", EXTRA_CATALOG)
    helpers.set_trino_catalogs(juju, [*CATALOGS, EXTRA_CATALOG])
    sources = _wait_for_sources(
        juju,
        graphql,
        lambda found: EXTRA_CATALOG in found,
        f"no ingestion source was created for the new '{EXTRA_CATALOG}' catalog",
    )
    assert set(sources) == {*CATALOGS, EXTRA_CATALOG}
    assert _recipe(sources[EXTRA_CATALOG])["source"]["config"]["database"] == EXTRA_CATALOG

    logger.info("Removing catalog '%s' from Trino again", EXTRA_CATALOG)
    helpers.set_trino_catalogs(juju, CATALOGS)
    _prove_reconciled(juju, graphql, CATALOGS[0])

    remaining = _managed_sources(graphql)
    assert EXTRA_CATALOG in remaining, "the ingestion source of a removed catalog was deleted"
    assert _urns(remaining) == _urns(sources), "reconciliation replaced ingestion sources instead of keeping them"


def test_relation_broken_keeps_ingestions(trino_stack: jubilant.Juju, graphql):
    """Removing the relation leaves every ingestion source and edit in place."""
    juju = trino_stack
    before = _managed_sources(graphql)

    logger.info("Removing the trino-catalog relation")
    juju.remove_relation(f"{helpers.APP_NAME}:trino-catalog", f"{helpers.TRINO_NAME}:trino-catalog")
    helpers.wait_for_all_active(juju, [helpers.APP_NAME, helpers.TRINO_NAME], timeout=15 * 60)

    # There is no positive signal for "nothing was deleted", so give the charm
    # more reconciliations than it would need to delete anything.
    time.sleep(3 * 60)

    after = _managed_sources(graphql)
    assert _urns(after) == _urns(before), "ingestion sources were lost when the relation was removed"
    config = _recipe(after[EDITED_CATALOG])["source"]["config"]
    assert config["profiling"] == OPERATOR_PROFILING
    assert config["schema_pattern"] == OPERATOR_SCHEMA_PATTERN


def test_reintegration_reuses_the_existing_ingestions(trino_stack: jubilant.Juju, graphql):
    """Re-adding the relation adopts the existing sources instead of duplicating them."""
    juju = trino_stack
    before = _managed_sources(graphql)

    logger.info("Re-adding the trino-catalog relation")
    juju.integrate(f"{helpers.APP_NAME}:trino-catalog", f"{helpers.TRINO_NAME}:trino-catalog")
    helpers.wait_for_all_active(juju, [helpers.APP_NAME, helpers.TRINO_NAME], timeout=15 * 60)
    _prove_reconciled(juju, graphql, EDITED_CATALOG)

    after = _managed_sources(graphql)
    assert _urns(after) == _urns(before), "re-adding the relation did not reuse the existing ingestion sources"

    # The new relation issues a new Trino user, which the charm has to pick up.
    config = _recipe(after[EDITED_CATALOG])["source"]["config"]
    assert config["username"].startswith(f"app-{helpers.APP_NAME}-")
    assert config["profiling"] == OPERATOR_PROFILING, "operator edits were reset when the relation came back"
