# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""DataHub charm integration test config."""

import logging
import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path
from typing import Dict

import jubilant
import pytest
from pytest import FixtureRequest

logger = logging.getLogger(__name__)

LXD_CONTROLLER = "localhost-localhost"
K8S_CLOUD = "canonical-k8s"


@pytest.fixture(scope="session", autouse=True)
def setup_hybrid_cloud():
    """Ensure LXD and Canonical Kubernetes are cross-configured before any tests run."""
    logger.info("Bootstrapping hybrid cloud environment.")

    # Configure kernel parameters for OpenSearch.
    subprocess.run(
        [
            "/usr/bin/sudo",
            "sysctl",
            "-w",
            "vm.max_map_count=262144",
            "vm.swappiness=0",
            "net.ipv4.tcp_retries2=5",
            "fs.file-max=1048576",
        ],
        check=True,
    )  # nosec B603

    # Bootstrap LXD controller
    subprocess.run(["/snap/bin/juju", "bootstrap", "localhost", LXD_CONTROLLER], check=False)  # nosec B603

    # Obtain the Canonical Kubernetes admin kubeconfig.
    kubeconfig = os.path.expanduser("~/.kube/config")

    # Register the canonical-k8s cluster as a cloud on the LXD controller.
    # `juju add-k8s` reads the cluster + credential from $KUBECONFIG.
    result = subprocess.run(
        ["/snap/bin/juju", "add-k8s", K8S_CLOUD, "--client", "--controller", LXD_CONTROLLER],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "KUBECONFIG": kubeconfig},
    )  # nosec B603
    if result.returncode != 0:
        if "already exists" in result.stderr or "already exists" in result.stdout:
            logger.info("Canonical Kubernetes cloud already configured")
        else:
            raise RuntimeError(f"Failed to add canonical-k8s cloud.\nStdout: {result.stdout}\nStderr: {result.stderr}")


def _collect_juju_logs_if_failed(request: FixtureRequest, juju: jubilant.Juju) -> None:
    """Print Juju logs at teardown time when tests fail."""
    if not request.session.testsfailed:
        return
    logger.info("Collecting Juju logs from model '%s'", juju.model)
    log = juju.debug_log(limit=1000)
    print(log, end="", file=sys.stderr)


@pytest.fixture(scope="session")
def charm(request: FixtureRequest) -> Path:
    """Return the path to charm package to deploy."""
    charm_file = request.config.getoption("--charm-file")
    if charm_file:
        charm_path = Path(charm_file[0]).expanduser().resolve()
        if not charm_path.exists():
            raise FileNotFoundError(f"Charm does not exist: {charm_path}")
        return charm_path

    charm_path_env = os.environ.get("CHARM_PATH")
    if charm_path_env:
        charm_path = Path(charm_path_env).expanduser().resolve()
        if not charm_path.exists():
            raise FileNotFoundError(f"Charm does not exist: {charm_path}")
        return charm_path

    charm_paths = list(Path(".").glob("*.charm"))
    if not charm_paths:
        raise FileNotFoundError("No .charm file in current directory")
    if len(charm_paths) > 1:
        path_list = ", ".join(str(path) for path in charm_paths)
        raise ValueError(f"More than one .charm file in current directory: {path_list}")
    return charm_paths[0].resolve()


@pytest.fixture(scope="session")
def rock_resources(request: FixtureRequest) -> Dict[str, str]:
    """Provide the mapping of rock resources deployed locally by operator-workflows."""
    resources = {
        "datahub-actions": str(request.config.getoption("--datahub-actions-image")),
        "datahub-frontend": str(request.config.getoption("--datahub-frontend-image")),
        "datahub-gms": str(request.config.getoption("--datahub-gms-image")),
    }

    missing = [name for name, value in resources.items() if value in {"", "None", "none", None}]
    if missing:
        options = {
            "datahub-actions": "--datahub-actions-image",
            "datahub-frontend": "--datahub-frontend-image",
            "datahub-gms": "--datahub-gms-image",
        }
        missing_opts = ", ".join(options[name] for name in missing)
        raise ValueError(f"Missing required resource image options: {missing_opts}")

    return resources


@pytest.fixture(scope="function")
def solo_juju(request: FixtureRequest) -> jubilant.Juju:
    """Create a temporary K8s model for isolated deploy-only test.

    Storage uses the cluster default StorageClass (Canonical k8s `local-storage`).
    """
    with jubilant.temp_model(cloud=K8S_CLOUD) as juju:
        juju.wait_timeout = 30 * 60
        yield juju
        _collect_juju_logs_if_failed(request, juju)


@pytest.fixture(scope="module")
def k8s_juju(request: FixtureRequest) -> jubilant.Juju:
    """Create a temporary K8s model used by full deployment tests.

    Storage uses the cluster default StorageClass (Canonical k8s `local-storage`).
    """
    with jubilant.temp_model(cloud=K8S_CLOUD) as juju:
        juju.wait_timeout = 30 * 60
        yield juju
        _collect_juju_logs_if_failed(request, juju)


@pytest.fixture(scope="module")
def lxd_juju(request: FixtureRequest) -> jubilant.Juju:
    """Create a temporary LXD model used by dependency tests."""
    with jubilant.temp_model(cloud="localhost") as juju:
        juju.wait_timeout = 30 * 60
        yield juju
        _collect_juju_logs_if_failed(request, juju)
