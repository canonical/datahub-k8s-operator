# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import logging
from pathlib import Path
from typing import Dict, Tuple

import yaml
from juju.model import Model
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

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

KAFKA_NAME = "kafka"
KAFKA_CHANNEL = "3/stable"
KAFKA_OFFER_NAME = "kafka-client"
OPENSEARCH_NAME = "opensearch"
OPENSEARCH_CHANNEL = "2/edge"
OPENSEARCH_OFFER_NAME = "os-client"
POSTGRES_NAME = "postgresql"
POSTGRES_CHANNEL = "14/stable"
POSTGRES_OFFER_NAME = "pg-client"
CERTIFICATES_NAME = "self-signed-certificates"
CERTIFICATES_CHANNEL = "latest/stable"
ZOOKEPER_NAME = "zookeeper"
ZOOKEEPER_CHANNEL = "3/stable"


async def add_juju_secrets(ops_test: OpsTest) -> Dict[str, Tuple[str, str]]:
    """Add required Juju user secrets to model.

    Args:
        ops_test: PyTest object.

    Returns:
        Dictionary of secrets to IDs.
    """
    keys_name = "encryption_keys"
    secrets = await ops_test.model.list_secrets()
    secrets = [secret for secret in secrets if secret.label == keys_name]
    if secrets:
        keys_id = secrets[0].uri.split(":")[-1]
    else:
        keys_secret = await ops_test.model.add_secret(
            name=keys_name,
            data_args=["gms-key=secret_secret123", "frontend-key=secret_secret123"],
        )
        keys_id = keys_secret.split(":")[-1]

    ret = {
        "encryption-keys-secret": (keys_id, keys_name),
    }
    return ret


async def build_offer_url(ops_test: OpsTest, model: Model, offer_name: str) -> str:
    """Build offer URL from inputs.

    Args:
        ops_test: PyTest object that holds the model.
        model: Juju model to get offer information.
        offer_name: Name of the offer.

    Returns:
        URL for the offer.
    """
    controller = await model.get_controller()
    username = controller.get_current_username()
    controller_name = ops_test.controller_name
    model_name = model.name
    offer_url = f"{controller_name}:{username}/{model_name}.{offer_name}"
    return offer_url


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


async def get_unit_url(ops_test: OpsTest, application: str, unit: str, port: int, protocol: str = "http"):
    """Return unit URL from the model.

    Args:
        ops_test: PyTest object.
        application: Name of the application.
        unit: Number of the unit.
        port: Port number of the URL.
        protocol: Transfer protocol (default: http).

    Returns:
        Unit URL of the form {protocol}://{address}:{port}
    """
    status = await ops_test.model.get_status()
    address = status["applications"][application]["units"][f"{application}/{unit}"]["address"]
    return f"{protocol}://{address}:{port}"
