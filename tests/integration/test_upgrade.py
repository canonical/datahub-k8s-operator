#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrade regression test: refresh the published charm to the local build."""

import logging
from pathlib import Path

import helpers
import jubilant
import pytest
import requests

logger = logging.getLogger(__name__)

BASELINE_CHANNEL = "latest/edge"


def _wait_can_login(juju: jubilant.Juju, password: str, timeout: float = 15 * 60) -> None:
    """Wait until the frontend accepts an admin login on unit 0."""
    base_url = helpers.get_unit_url(juju, helpers.APP_NAME, 0, 9002)
    url = f"{base_url}/logIn"

    def _can_login(_: jubilant.Status) -> bool:
        """Attempt a login and return True on HTTP 200."""
        try:
            response = requests.post(url, json={"username": "datahub", "password": password}, timeout=10)
            return response.status_code == 200
        except requests.RequestException as exc:
            logger.info("Login attempt failed: %s", exc)
            return False

    juju.wait(_can_login, timeout=timeout, delay=10, successes=1)


def _assert_serving(juju: jubilant.Juju, password: str) -> None:
    """Assert the frontend serves a login and GMS serves a versioned /config."""
    base_url = helpers.get_unit_url(juju, helpers.APP_NAME, 0, 9002)
    login = requests.post(
        f"{base_url}/logIn",
        json={"username": "datahub", "password": password},
        timeout=15,
    )
    assert login.status_code == 200, f"frontend login failed: {login.status_code}"

    gms_url = helpers.get_unit_url(juju, helpers.APP_NAME, 0, 8080)
    config = requests.get(f"{gms_url}/config", timeout=15)
    assert config.status_code == 200, f"GMS /config returned {config.status_code}"
    version = config.json().get("versions", {}).get("acryldata/datahub", {}).get("version")
    assert version and version != "null", f"GMS /config returned invalid version: {version!r}"


@pytest.fixture(scope="module")
def edge_stack(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju) -> jubilant.Juju:
    """Deploy the published (edge) charm with the full dependency stack, active."""
    logger.info("Deploying '%s' from channel '%s'", helpers.APP_NAME, BASELINE_CHANNEL)
    helpers.deploy_charm(k8s_juju, channel=BASELINE_CHANNEL)

    logger.info("Deploying LXD dependencies")
    helpers.deploy_lxd_dependencies(lxd_juju)

    logger.info("Consuming offers and integrating DataHub")
    helpers.consume_and_integrate(k8s_juju, lxd_juju)

    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME], timeout=30 * 60)
    return k8s_juju


def test_refresh_from_edge(edge_stack: jubilant.Juju, charm: Path, rock_resources: dict):
    """The published charm refreshes in place to the local build and keeps serving."""
    juju = edge_stack

    logger.info("Verifying the edge baseline serves before refresh")
    password_before = helpers.get_admin_password(juju)
    _wait_can_login(juju, password_before)
    _assert_serving(juju, password_before)

    logger.info("Refreshing '%s' to the local charm + local rocks", helpers.APP_NAME)
    juju.refresh(helpers.APP_NAME, path=charm, resources=rock_resources)

    # Let the upgrade-charm / config-changed reconcile churn settle.
    juju.wait(lambda status: jubilant.all_agents_idle(status, helpers.APP_NAME), timeout=10 * 60)
    helpers.wait_for_all_active(juju, [helpers.APP_NAME], timeout=30 * 60)

    logger.info("Verifying the refreshed charm still serves with a stable password")
    password_after = helpers.get_admin_password(juju)
    assert password_after == password_before, "admin password changed after refresh"

    _wait_can_login(juju, password_after)
    _assert_serving(juju, password_after)
