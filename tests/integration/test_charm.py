#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Run integration tests."""

import logging
from pathlib import Path
from typing import Tuple

import pytest
import requests
from helpers import APP_NAME, deploy_charm, get_unit_url
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.mark.abort_on_fail
class TestDeployment:
    """Integration tests for DataHub deployment."""

    @pytest.mark.usefixtures("charm")
    async def test_build_and_deploy_solo(self, ops_test: OpsTest, charm: Path):
        """Build the charm-under-test and deploy it by itself.

        Assert on the unit status before any relations/configurations take place.
        """
        # Deploy the charm and wait for blocked/idle status
        # We expect 'blocked' without dependencies
        await deploy_charm(ops_test, charm),
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=1000)

        # Cleanup
        await ops_test.model.remove_application(APP_NAME, block_until_done=True, force=True, destroy_storage=True)

    @pytest.mark.usefixtures("deploy")
    async def test_deploy_full(self, ops_test: OpsTest, deploy: Tuple[str, str]):
        """Build the charm-under-test and deploy it with the entire ecosystem."""
        k8s_model, _ = deploy

        logger.info("building unit url")
        with ops_test.model_context(k8s_model):
            base_url = await get_unit_url(ops_test, APP_NAME, 0, 9002)
            url = f"{base_url}/admin"

        logger.info("making request to: '%s'", url)
        response = requests.get(url, timeout=300)
        logger.info("response: %s", response.json())
        assert response.status_code == 200
