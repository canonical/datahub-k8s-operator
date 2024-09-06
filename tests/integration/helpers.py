# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

from pathlib import Path
from typing import Dict, Tuple

import yaml
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
RESOURCE_NAMES = [
    "datahub-actions",
    "datahub-frontend",
    "datahub-gms",
    "datahub-kafka-setup",
    "datahub-opensearch-setup",
    "datahub-postgresql-setup",
    "datahub-upgrade",
]
RESOURCES = {item: METADATA["resources"][item]["upstream-source"] for item in RESOURCE_NAMES}


async def add_juju_secrets(ops_test: OpsTest) -> Dict[str, Tuple[str, str]]:
    """Add required Juju user secrets to model.

    Args:
        ops_test: PyTest object.

    Returns:
        Dictionary of secrets to IDs.
    """
    keys_name = "encryption_keys"
    keys_secret = await ops_test.model.add_secret(
        name=keys_name,
        data_args=["gms-key=secret_secret123", "frontend-key=secret_secret123"],
    )

    keys_id = keys_secret.split(":")[-1]
    ret = {
        "encryption-keys-secret": (keys_id, keys_name),
    }
    return ret


async def deploy_charm(ops_test: OpsTest, charm: Path) -> None:
    """Deploy the charm with the given config.

    Args:
        ops_test: PyTest object.
        charm: Path to charm package.
    """
    secrets = await add_juju_secrets(ops_test)
    config = {"encryption-keys-secret-id": secrets["encryption-keys-secret"][0]}

    await ops_test.model.deploy(charm, resources=RESOURCES, config=config, application_name=APP_NAME)
    await ops_test.model.grant_secret(secrets["encryption-keys-secret"][1], APP_NAME)
