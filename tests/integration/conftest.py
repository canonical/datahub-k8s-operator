# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""DataHub charm integration test config."""

import logging
from pathlib import Path
from typing import Union

import pytest_asyncio
from pytest import FixtureRequest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest_asyncio.fixture(scope="module", name="charm")
async def charm_fixture(request: FixtureRequest, ops_test: OpsTest) -> Union[str, Path]:
    """Fetch the path to charm."""
    charms = request.config.getoption("--charm-file")
    if not charms:
        charm = await ops_test.build_charm(".")
        assert charm, "Charm not built"
        return charm
    return charms[0]
