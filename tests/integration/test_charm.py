#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Run integration tests."""

import asyncio
import logging
from pathlib import Path

import helpers
import pytest
import requests
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
        label = "deploy-solo"
        k8s_model = await helpers.ensure_model(label, ops_test, "microk8s", "k8s")
        with ops_test.model_context(k8s_model):
            await helpers.deploy_charm(ops_test, charm)
            await ops_test.model.wait_for_idle(apps=[helpers.APP_NAME], status="blocked", timeout=1000)

    # TODO (mertalpt): Implement the following to avoid having to use an `xlarge` runner.
    # https://github.com/canonical/opensearch-operator/blob/2/edge/tests/integration/helpers.py#L149
    @pytest.mark.usefixtures("charm")
    async def test_deploy_full(self, ops_test: OpsTest, charm: Path):
        """Build the charm-under-test and deploy it with the entire ecosystem."""
        # Setup
        label = "full-deploy"
        k8s_model = await helpers.ensure_model(label, ops_test, "microk8s", "k8s")
        lxd_model = await helpers.ensure_model(label, ops_test, "localhost", "lxd")

        with ops_test.model_context(lxd_model):
            async with ops_test.fast_forward():
                # Deploy dependencies
                logger.info("Deploying LXD dependencies")
                await asyncio.gather(
                    ops_test.model.deploy(helpers.KAFKA_NAME, channel=helpers.KAFKA_CHANNEL),
                    ops_test.model.deploy(helpers.OPENSEARCH_NAME, channel=helpers.OPENSEARCH_CHANNEL, num_units=3),
                    ops_test.model.deploy(helpers.POSTGRES_NAME, channel=helpers.POSTGRES_CHANNEL),
                    ops_test.model.deploy(helpers.CERTIFICATES_NAME, channel=helpers.CERTIFICATES_CHANNEL),
                    ops_test.model.deploy(helpers.ZOOKEPER_NAME, channel=helpers.ZOOKEEPER_CHANNEL),
                )

                # Wait for the dependencies to settle
                logger.info(
                    "Waiting for '%s, %s, %s' to settle into 'active-idle'",
                    helpers.POSTGRES_NAME,
                    helpers.CERTIFICATES_NAME,
                    helpers.ZOOKEPER_NAME,
                )
                await ops_test.model.wait_for_idle(
                    apps=[helpers.POSTGRES_NAME, helpers.CERTIFICATES_NAME, helpers.ZOOKEPER_NAME],
                    status="active",
                    timeout=10 * 60,
                )

                logger.info(
                    "Waiting for '%s, %s' to settle into 'blocked-idle'", helpers.KAFKA_NAME, helpers.OPENSEARCH_NAME
                )
                await ops_test.model.wait_for_idle(
                    apps=[helpers.KAFKA_NAME, helpers.OPENSEARCH_NAME],
                    status="blocked",
                    raise_on_blocked=False,
                    timeout=30 * 60,
                )

            # Relate the dependencies
            logger.info("Integrating dependencies")
            await asyncio.gather(
                ops_test.model.integrate(helpers.KAFKA_NAME, helpers.ZOOKEPER_NAME),
                ops_test.model.integrate(helpers.OPENSEARCH_NAME, helpers.CERTIFICATES_NAME),
            )

            # Wait for the dependencies to settle
            logger.info("Waiting for dependencies to settle into 'active-idle'")
            await ops_test.model.wait_for_idle(
                apps=[
                    helpers.KAFKA_NAME,
                    helpers.OPENSEARCH_NAME,
                    helpers.POSTGRES_NAME,
                    helpers.CERTIFICATES_NAME,
                    helpers.ZOOKEPER_NAME,
                ],
                status="active",
                timeout=15 * 60,
            )

            # Create offers for DataHub to consume
            logger.info("Creating offers")
            await asyncio.gather(
                ops_test.model.create_offer(
                    endpoint="kafka-client", application_name=helpers.KAFKA_NAME, offer_name=helpers.KAFKA_OFFER_NAME
                ),
                ops_test.model.create_offer(
                    endpoint="opensearch-client",
                    application_name=helpers.OPENSEARCH_NAME,
                    offer_name=helpers.OPENSEARCH_OFFER_NAME,
                ),
                ops_test.model.create_offer(
                    endpoint="database", application_name=helpers.POSTGRES_NAME, offer_name=helpers.POSTGRES_OFFER_NAME
                ),
            )

        with ops_test.model_context(k8s_model):
            async with ops_test.fast_forward(fast_interval="5m"):
                # Deploy DataHub
                logger.info("Deploying '%s'", helpers.APP_NAME)
                await helpers.deploy_charm(ops_test, charm)

                # TODO (mertalpt): Find a way to avoid doing this.
                # Gives `pebble_ready` time to run before making relations.
                # I suspect it is a problem because gigabytes of OCI images are not cached
                # and take a long time to be downloaded.
                await asyncio.sleep(10 * 60)

                # Wait for DataHub to settle
                logger.info("Waiting for '%s' to settle into 'blocked-idle'", helpers.APP_NAME)
                await ops_test.model.wait_for_idle(
                    apps=[helpers.APP_NAME],
                    status="blocked",
                    raise_on_blocked=False,
                    timeout=10 * 60,
                )

                # Consume offers
                logger.info("Consuming offers")
                await ops_test.juju("consume", f"{lxd_model}.{helpers.KAFKA_OFFER_NAME}")
                await ops_test.juju("consume", f"{lxd_model}.{helpers.OPENSEARCH_OFFER_NAME}")
                await ops_test.juju("consume", f"{lxd_model}.{helpers.POSTGRES_OFFER_NAME}")

                # Relate to offers
                logger.info("Integrating to '%s'", helpers.KAFKA_OFFER_NAME)
                await ops_test.model.integrate(helpers.APP_NAME, helpers.KAFKA_OFFER_NAME)
                logger.info(
                    "Waiting for '%s' to settle into 'blocked-idle' post '%s' integration",
                    helpers.APP_NAME,
                    helpers.KAFKA_OFFER_NAME,
                )
                await asyncio.sleep(2 * 60)
                await ops_test.model.wait_for_idle(
                    apps=[helpers.APP_NAME],
                    status="blocked",
                    raise_on_blocked=False,
                    timeout=10 * 60,
                )

                logger.info("Integrating to '%s'", helpers.OPENSEARCH_OFFER_NAME)
                await ops_test.model.integrate(helpers.APP_NAME, helpers.OPENSEARCH_OFFER_NAME)
                logger.info(
                    "Waiting for '%s' to settle into 'blocked-idle' post '%s' integration",
                    helpers.APP_NAME,
                    helpers.OPENSEARCH_OFFER_NAME,
                )
                await asyncio.sleep(2 * 60)
                await ops_test.model.wait_for_idle(
                    apps=[helpers.APP_NAME],
                    status="blocked",
                    raise_on_blocked=False,
                    timeout=10 * 60,
                )

                logger.info("Integrating to '%s'", helpers.POSTGRES_OFFER_NAME)
                await ops_test.model.integrate(helpers.APP_NAME, helpers.POSTGRES_OFFER_NAME)
                logger.info(
                    "Waiting for '%s' to settle into 'active-idle' post '%s' integration",
                    helpers.APP_NAME,
                    helpers.POSTGRES_OFFER_NAME,
                )
                await asyncio.sleep(2 * 60)
                await ops_test.model.wait_for_idle(
                    apps=[helpers.APP_NAME],
                    status="active",
                    raise_on_blocked=False,
                    timeout=10 * 60,
                )

                # TODO (mertalpt): Find a way to avoid this.
                # Frontend takes some time to serve requests after settling.
                await asyncio.sleep(5 * 60)

        # Test
        with ops_test.model_context(k8s_model):
            logger.info("Building unit url")
            base_url = await helpers.get_unit_url(ops_test, helpers.APP_NAME, 0, 9002)

            with requests.session() as s:
                # Log in
                url = f"{base_url}/logIn"
                logging.info("Request to: '%s' - running", url)
                r = s.post(url, json={"username": "datahub", "password": "datahub"})
                assert r.status_code == 200
                logging.info("Request to: '%s' - passed", url)
