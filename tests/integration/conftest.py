# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""DataHub charm integration test config."""

import asyncio
import logging
from pathlib import Path
from typing import Tuple, Union

import helpers
import pytest_asyncio
from pytest import FixtureRequest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(scope="module")
async def charm(request: FixtureRequest, ops_test: OpsTest) -> Union[str, Path]:
    """Fetch the path to charm, after building it if necessary."""
    charm = request.config.getoption("--charm-file")
    if not charm:
        charm = await ops_test.build_charm(".")
        assert charm, "Charm not built."
    else:
        charm = charm[0]
    return charm


@pytest_asyncio.fixture(scope="module")
async def deploy(request: FixtureRequest, ops_test: OpsTest, charm: Path) -> Tuple[str, str]:
    """Build, deploy, and relate everything together."""
    label = (request.module.__name__).replace("_", "-")
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
        async with ops_test.fast_forward():
            # Deploy DataHub
            logger.info("Deploying '%s'", helpers.APP_NAME)
            await helpers.deploy_charm(ops_test, charm)

            # Wait for DataHub to settle
            logger.info("Waiting for '%s' to settle into 'active-idle'", helpers.APP_NAME)
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
            await ops_test.model.wait_for_idle(
                apps=[helpers.APP_NAME],
                status="active",
                raise_on_blocked=False,
                timeout=15 * 60,
            )

    return (k8s_model, lxd_model)

    # Return to check dependencies did not fail
    # logger.info("Checking dependency status post-integration")
    # with ops_test.model_context(lxd_model):
    #     async with ops_test.fast_forward():
    #         await ops_test.model.wait_for_idle(
    #             apps=[
    #                 helpers.KAFKA_NAME,
    #                 helpers.OPENSEARCH_NAME,
    #                 helpers.POSTGRES_NAME,
    #                 helpers.CERTIFICATES_NAME,
    #                 helpers.ZOOKEPER_NAME,
    #             ],
    #             status="active",
    #             raise_on_blocked=False,
    #             timeout=10 * 60,
    #         )
