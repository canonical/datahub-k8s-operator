#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Run integration tests."""

import logging
from pathlib import Path

import helpers
import jubilant
import pytest
import requests

logger = logging.getLogger(__name__)


def _get_password(juju: jubilant.Juju) -> str:
    """Get admin password via the get-password action."""
    action_output = juju.run(f"{helpers.APP_NAME}/0", "get-password", wait=60)
    password = action_output.results.get("password", "")
    if not password:
        raise ValueError("get-password action did not return a password")
    logger.info("Fetched admin password via get-password action")
    return password


@pytest.fixture(scope="module")
def full_stack(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: Path, rock_resources: dict) -> jubilant.Juju:
    """Deploy full dependency stack and return deployed K8s model handle."""
    logger.info("Deploying '%s'", helpers.APP_NAME)
    helpers.deploy_charm(k8s_juju, charm, rock_resources)

    logger.info("Deploying LXD dependencies")
    lxd_juju.deploy(helpers.KAFKA_NAME, channel=helpers.KAFKA_CHANNEL, revision=helpers.KAFKA_REVISION)
    lxd_juju.deploy(
        helpers.OPENSEARCH_NAME,
        channel=helpers.OPENSEARCH_CHANNEL,
        num_units=2,
        revision=helpers.OPENSEARCH_REVISION,
    )
    lxd_juju.deploy(helpers.POSTGRES_NAME, channel=helpers.POSTGRES_CHANNEL, revision=helpers.POSTGRES_REVISION)
    lxd_juju.deploy(
        helpers.CERTIFICATES_NAME,
        channel=helpers.CERTIFICATES_CHANNEL,
        revision=helpers.CERTIFICATES_REVISION,
    )
    lxd_juju.deploy(helpers.ZOOKEPER_NAME, channel=helpers.ZOOKEEPER_CHANNEL, revision=helpers.ZOOKEEPER_REVISION)

    logger.info("Waiting for LXD dependencies to settle")
    helpers.wait_for_apps_status(
        lxd_juju,
        {
            helpers.POSTGRES_NAME: "active",
            helpers.CERTIFICATES_NAME: "active",
            helpers.ZOOKEPER_NAME: "active",
            helpers.KAFKA_NAME: ("blocked", "waiting"),
            helpers.OPENSEARCH_NAME: ("blocked", "waiting"),
        },
        timeout=30 * 60,
        raise_on_error=False,
    )

    logger.info("Integrating LXD dependencies")
    lxd_juju.integrate(helpers.KAFKA_NAME, helpers.ZOOKEPER_NAME)
    lxd_juju.integrate(helpers.OPENSEARCH_NAME, helpers.CERTIFICATES_NAME)
    helpers.wait_for_all_active(
        lxd_juju,
        [
            helpers.KAFKA_NAME,
            helpers.OPENSEARCH_NAME,
            helpers.POSTGRES_NAME,
            helpers.CERTIFICATES_NAME,
            helpers.ZOOKEPER_NAME,
        ],
        timeout=20 * 60,
    )

    logger.info("Creating offers")
    lxd_juju.offer(helpers.KAFKA_NAME, endpoint="kafka-client", name=helpers.KAFKA_OFFER_NAME)
    lxd_juju.offer(helpers.OPENSEARCH_NAME, endpoint="opensearch-client", name=helpers.OPENSEARCH_OFFER_NAME)
    lxd_juju.offer(helpers.POSTGRES_NAME, endpoint="database", name=helpers.POSTGRES_OFFER_NAME)

    lxd_model = helpers.model_short_name(lxd_juju.model or "")
    if not lxd_model:
        raise ValueError("Could not determine LXD model name")

    logger.info("Consuming offers")
    k8s_juju.consume(f"{lxd_model}.{helpers.KAFKA_OFFER_NAME}")
    k8s_juju.consume(f"{lxd_model}.{helpers.OPENSEARCH_OFFER_NAME}")
    k8s_juju.consume(f"{lxd_model}.{helpers.POSTGRES_OFFER_NAME}")

    logger.info("Waiting for K8s applications to settle")
    helpers.wait_for_apps_status(
        k8s_juju,
        {
            helpers.APP_NAME: ("blocked", "waiting", "maintenance", "active"),
        },
        timeout=20 * 60,
        raise_on_error=False,
    )

    logger.info("Integrating consumed offers")
    k8s_juju.integrate(f"{helpers.APP_NAME}:kafka", helpers.KAFKA_OFFER_NAME)
    k8s_juju.integrate(f"{helpers.APP_NAME}:opensearch", helpers.OPENSEARCH_OFFER_NAME)
    k8s_juju.integrate(f"{helpers.APP_NAME}:db", helpers.POSTGRES_OFFER_NAME)

    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME], timeout=30 * 60)

    return k8s_juju


def test_build_and_deploy_solo(solo_juju: jubilant.Juju, charm: Path, rock_resources: dict):
    """Deploy the charm by itself and assert expected blocked status."""
    helpers.deploy_charm(solo_juju, charm, rock_resources)
    helpers.wait_for_apps_status(
        solo_juju,
        {
            helpers.APP_NAME: "blocked",
        },
        timeout=20 * 60,
        raise_on_error=False,
    )


def test_deploy_full(full_stack: jubilant.Juju):
    """Verify full stack deployment by authenticating to the frontend."""
    logger.info("Building unit URL")
    base_url = helpers.get_unit_url(full_stack, helpers.APP_NAME, 0, 9002)

    logger.info("Fetching admin password")
    admin_pwd = _get_password(full_stack)

    url = f"{base_url}/logIn"
    logger.info("Testing against URL: %s", url)

    def _can_login(_: jubilant.Status) -> bool:
        """Attempt login and return True on success."""
        try:
            response = requests.post(url, json={"username": "datahub", "password": admin_pwd}, timeout=10)

            if response.status_code == 200:
                return True

            logger.info(
                "Login failed | Status: %d | Body: %s | Headers: %s",
                response.status_code,
                response.text[:500],  # Truncate to avoid flooding logs
                response.headers,
            )
            return False

        except requests.RequestException as e:
            # This catches connection errors, timeouts, and DNS issues
            logger.info("Login connection error: %s", str(e))
            return False

    logger.info("Waiting for frontend to be ready")
    full_stack.wait(_can_login, timeout=15 * 60, delay=10, successes=1)

    response = requests.post(url, json={"username": "datahub", "password": admin_pwd}, timeout=10)
    assert response.status_code == 200

    logger.info("Verifying GMS workload version is embedded")
    gms_url = helpers.get_unit_url(full_stack, helpers.APP_NAME, 0, 8080)
    config_response = requests.get(f"{gms_url}/config", timeout=10)
    assert config_response.status_code == 200
    versions = config_response.json().get("versions", {})
    datahub_info = versions.get("acryldata/datahub", {})
    datahub_version = datahub_info.get("version")
    datahub_commit = datahub_info.get("commit")

    logger.info("DATAHUB_GMS_VERSION version=%s commit=%s", datahub_version, datahub_commit)
    assert (
        datahub_version and datahub_version != "null"
    ), f"DataHub /config returned invalid version: {datahub_version!r}"

    # Exercise the OpenSearch-backed search path. If the JVM truststore is
    # missing the OpenSearch CA, this surfaces as PKIX errors in the GraphQL
    # response.
    logger.info("Verifying GMS can serve search queries (OpenSearch TLS path)")
    session = requests.Session()
    login_response = session.post(url, json={"username": "datahub", "password": admin_pwd}, timeout=10)
    assert login_response.status_code == 200

    graphql_url = f"{base_url}/api/v2/graphql"
    query = {"query": ('{ searchAcrossEntities(input: {types: [DATASET], query: "*", count: 1})' " { total } }")}
    search_response = session.post(graphql_url, json=query, timeout=15)
    assert (
        search_response.status_code == 200
    ), f"GraphQL request returned {search_response.status_code}: {search_response.text[:500]}"

    body = search_response.json()
    errors = body.get("errors") or []
    ssl_errors = [e for e in errors if any(token in str(e) for token in ("PKIX", "SSL", "version='unknown'"))]
    logger.info(
        "DATAHUB_GMS_SEARCH errors=%d ssl_errors=%d",
        len(errors),
        len(ssl_errors),
    )
    assert not ssl_errors, f"SSL/trust errors during search: {ssl_errors}"


def test_reindex_action(full_stack: jubilant.Juju):
    """Run and verify the reindex action."""
    logger.info("Testing reindex action")
    action_output = full_stack.run(f"{helpers.APP_NAME}/0", "reindex", {"clean": True}, wait=30 * 60)
    logger.info("Action result: %s", action_output.results)

    assert "result" in action_output.results
    assert "command succeeded" in action_output.results["result"]
