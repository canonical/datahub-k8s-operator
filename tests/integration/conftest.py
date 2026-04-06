# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""DataHub charm integration test config."""

import logging
import subprocess  # nosec B404
import tempfile
from pathlib import Path
from typing import Dict, Union

import pytest
import pytest_asyncio
from pytest import FixtureRequest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def setup_hybrid_cloud():
    """Ensure LXD and MicroK8s are cross-configured before any tests run."""
    logger.info("Bootstrapping hybrid cloud environment.")

    # Configure kernel parameters for OpenSearch
    sysctl_config = "vm.max_map_count=262144\n" "vm.swappiness=0\n" "net.ipv4.tcp_retries2=5\n" "fs.file-max=1048576\n"
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(sysctl_config)
        conf_file = f.name
    subprocess.run(["/usr/bin/sudo", "bash", "-c", f"cat {conf_file} >> /etc/sysctl.conf"], check=True)  # nosec B603
    subprocess.run(["/usr/bin/sudo", "sysctl", "-p"], check=True)  # nosec B603

    # Bootstrap LXD controller
    subprocess.run(["/snap/bin/juju", "bootstrap", "localhost", "localhost-localhost"], check=False)  # nosec B603

    # Tweak microk8s API address for LXD access
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        subprocess.run(["/usr/bin/sudo", "microk8s.config"], stdout=f, check=True)  # nosec B603
        client_config = f.name
    subprocess.run(
        ["/usr/bin/sudo", "cp", client_config, "/var/snap/microk8s/current/credentials/client.config"],
        check=True,
    )  # nosec B603

    # Add microk8s cloud to the LXD controller
    subprocess.run(
        [
            "/usr/bin/sudo",
            "-g",
            "snap_microk8s",
            "-E",
            "juju",
            "add-cloud",
            "microk8s",
            "--controller",
            "localhost-localhost",
            "--credential",
            "microk8s",
        ],
        check=False,
    )  # nosec B603


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


@pytest.fixture(scope="module")
def rock_resources(request: FixtureRequest) -> Dict[str, str]:
    """Provide the mapping of rock resources deployed locally by operator-workflows."""
    return {
        "datahub-actions": str(request.config.getoption("--datahub-actions-image")),
        "datahub-frontend": str(request.config.getoption("--datahub-frontend-image")),
        "datahub-gms": str(request.config.getoption("--datahub-gms-image")),
    }
