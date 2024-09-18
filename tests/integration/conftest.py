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
