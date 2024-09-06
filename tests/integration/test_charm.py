#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Run integration tests."""

import asyncio
import logging

import pytest
from helpers import APP_NAME, deploy_charm
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


class TestDeployment:
    """Integration tests for DataHub deployment."""

    @pytest.mark.abort_on_fail
    @pytest.mark.usefixtures("charm")
    async def test_build_and_deploy(self, ops_test: OpsTest, charm: str):
        """Build the charm-under-test and deploy it by itself.

        Assert on the unit status before any relations/configurations take place.
        """
        # Deploy the charm and wait for blocked/idle status
        # We expect 'blocked' without dependencies
        await asyncio.gather(
            deploy_charm(ops_test, charm),
            ops_test.model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=1000),
        )
