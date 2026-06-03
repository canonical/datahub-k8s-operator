#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Scaling test: DataHub scales out to 3 units and back in to 1."""

import logging
from pathlib import Path

import helpers
import jubilant
import pytest
import requests

logger = logging.getLogger(__name__)


def _wait_can_login(juju: jubilant.Juju, unit: int, password: str, timeout: float = 15 * 60) -> None:
    """Wait until the frontend on a given unit accepts an admin login."""
    base_url = helpers.get_unit_url(juju, helpers.APP_NAME, unit, 9002)
    url = f"{base_url}/logIn"

    def _can_login(_: jubilant.Status) -> bool:
        """Attempt a login against this unit and return True on HTTP 200."""
        try:
            response = requests.post(url, json={"username": "datahub", "password": password}, timeout=10)
            return response.status_code == 200
        except requests.RequestException as exc:
            logger.info("Login attempt on unit %d failed: %s", unit, exc)
            return False

    juju.wait(_can_login, timeout=timeout, delay=10, successes=1)


@pytest.fixture(scope="module")
def scaled_stack(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: Path, rock_resources: dict) -> jubilant.Juju:
    """Deploy the local charm with the full dependency stack at one unit, active."""
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
    return k8s_juju


def test_scale_out_and_in(scaled_stack: jubilant.Juju):
    """Scale 1 -> 3 -> 1, staying Active and serving throughout."""
    juju = scaled_stack

    password = helpers.get_admin_password(juju, unit=0)

    logger.info("Scaling out to 3 units")
    juju.add_unit(helpers.APP_NAME, num_units=2)
    juju.wait(
        lambda status: jubilant.all_active(status, helpers.APP_NAME) and len(status.apps[helpers.APP_NAME].units) == 3,
        error=lambda status: jubilant.any_error(status, helpers.APP_NAME),
        timeout=30 * 60,
    )

    logger.info("Verifying every unit serves the frontend with the same admin password")
    for unit in range(3):
        assert helpers.get_admin_password(juju, unit=unit) == password, f"unit {unit} reports a different password"
        _wait_can_login(juju, unit, password)

    logger.info("Scaling back in to 1 unit")
    juju.remove_unit(helpers.APP_NAME, num_units=2)
    juju.wait(
        lambda status: jubilant.all_active(status, helpers.APP_NAME) and len(status.apps[helpers.APP_NAME].units) == 1,
        error=lambda status: jubilant.any_error(status, helpers.APP_NAME),
        timeout=20 * 60,
    )

    logger.info("Verifying the surviving unit still serves")
    _wait_can_login(juju, 0, password)
