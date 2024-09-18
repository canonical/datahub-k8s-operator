# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""DataHub charm integration test config."""

import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator

import helpers
import pytest_asyncio
from juju.juju import Juju
from pytest import FixtureRequest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(scope="module")
async def charm(request: FixtureRequest, ops_test: OpsTest) -> Path:
    """Fetch the path to charm, after building it if necessary."""
    charm = request.config.getoption("--charm-file")
    if not charm:
        charm = await ops_test.build_charm(".")
        assert charm, "Charm not built"
    else:
        charm = charm[0]
    return Path(charm)


@pytest_asyncio.fixture(scope="module")
async def lxd_ops_test(request: FixtureRequest, ops_test: OpsTest, tmp_path_factory) -> AsyncGenerator[OpsTest, None]:
    """Return a OpsTest fixture for lxd, create a lxd controller if it does not exist."""
    # Ref: https://github.com/canonical/paas-app-charmer/blob/4ec71620038c17dd20e0be72ac5c05f8b62d1318/tests/integration/conftest.py#L19  # noqa: W505
    if "lxd" not in Juju().get_controllers():
        # Bootstrap a new controller
        await ops_test.juju("bootstrap", "localhost", "lxd", check=True)

    ops_test = OpsTest(request, tmp_path_factory)
    ops_test.controller_name = "lxd"
    await ops_test._setup_model()
    yield ops_test
    await ops_test._cleanup_models()


@pytest_asyncio.fixture(scope="module")
async def deploy(request: FixtureRequest, ops_test: OpsTest, lxd_ops_test: OpsTest, charm: Path) -> None:
    """Build, deploy, and relate everything together."""
    # Deploy components
    deploy_result = asyncio.gather(
        helpers.deploy_charm(ops_test, charm),
        lxd_ops_test.model.deploy(helpers.KAFKA_NAME, channel=helpers.KAFKA_CHANNEL),
        lxd_ops_test.model.deploy(helpers.OPENSEARCH_NAME, channel=helpers.OPENSEARCH_CHANNEL, num_units=2),
        lxd_ops_test.model.deploy(helpers.POSTGRES_NAME, channel=helpers.POSTGRES_CHANNEL),
        lxd_ops_test.model.deploy(helpers.CERTIFICATES_NAME, channel=helpers.CERTIFICATES_CHANNEL),
        lxd_ops_test.model.deploy(helpers.ZOOKEPER_NAME, channel=helpers.ZOOKEEPER_CHANNEL),
    )

    # Wait for the dependencies to settle
    await deploy_result
    async with lxd_ops_test.fast_forward():
        await lxd_ops_test.model.wait_for_idle(
            apps=[helpers.POSTGRES_NAME, helpers.CERTIFICATES_NAME, helpers.ZOOKEPER_NAME],
            status="active",
            timeout=10 * 60,
        )

        await lxd_ops_test.model.wait_for_idle(
            apps=[helpers.KAFKA_NAME, helpers.OPENSEARCH_NAME],
            status="blocked",
            timeout=10 * 60,
        )

    # Relate the dependencies
    await asyncio.gather(
        lxd_ops_test.model.integrate(helpers.KAFKA_NAME, helpers.ZOOKEPER_NAME),
        lxd_ops_test.model.integrate(helpers.OPENSEARCH_NAME, helpers.CERTIFICATES_NAME),
    )

    # Wait for the dependencies to settle
    async with lxd_ops_test.fast_forward():
        await lxd_ops_test.model.wait_for_idle(
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
    await asyncio.gather(
        lxd_ops_test.model.create_offer(
            endpoint="kafka-client", application_name=helpers.KAFKA_NAME, offer_name=helpers.KAFKA_OFFER_NAME
        ),
        lxd_ops_test.model.create_offer(
            endpoint="opensearch-client",
            application_name=helpers.OPENSEARCH_NAME,
            offer_name=helpers.OPENSEARCH_OFFER_NAME,
        ),
        lxd_ops_test.model.create_offer(
            endpoint="database", application_name=helpers.POSTGRES_NAME, offer_name=helpers.POSTGRES_OFFER_NAME
        ),
    )

    # Wait for DataHub to settle
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[helpers.APP_NAME],
            status="blocked",
            timeout=10 * 60,
        )

    # Consume offers
    kafka_offer = await helpers.build_offer_url(lxd_ops_test, lxd_ops_test.model, helpers.KAFKA_OFFER_NAME)
    os_offer = await helpers.build_offer_url(lxd_ops_test, lxd_ops_test.model, helpers.OPENSEARCH_OFFER_NAME)
    pg_offer = await helpers.build_offer_url(lxd_ops_test, lxd_ops_test.model, helpers.POSTGRES_OFFER_NAME)

    kafka_saas = await ops_test.model.consume(kafka_offer)
    os_saas = await ops_test.model.consume(os_offer)
    pg_saas = await ops_test.model.consume(pg_offer)

    # Relate to offers
    await asyncio.gather(
        ops_test.model.integrate(helpers.APP_NAME, kafka_saas),
        ops_test.model.integrate(helpers.APP_NAME, os_saas),
        ops_test.model.integrate(helpers.APP_NAME, pg_saas),
    )

    # Wait for DataHub to settle
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[helpers.APP_NAME],
            status="active",
            timeout=15 * 60,
        )
