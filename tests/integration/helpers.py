# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import logging
import uuid
from pathlib import Path
from typing import Dict, Literal, Tuple

import yaml
from pytest_operator.plugin import OpsTest

import exceptions

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
OPENSEARCH_CHANNEL = "2/stable"
OPENSEARCH_OFFER_NAME = "os-client"
POSTGRES_NAME = "postgresql"
POSTGRES_CHANNEL = "14/stable"
POSTGRES_OFFER_NAME = "pg-client"
CERTIFICATES_NAME = "self-signed-certificates"
CERTIFICATES_CHANNEL = "latest/stable"
ZOOKEPER_NAME = "zookeeper"
ZOOKEEPER_CHANNEL = "3/stable"
INGRESS_NAME = "nginx-ingress-integrator"
INGRESS_CHANNEL = "stable"


# Used to make models unique between parallel runs.
__RUN_ID = uuid.uuid4().hex


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

    # Setup the ingress.
    await ops_test.model.deploy(INGRESS_NAME, channel=INGRESS_CHANNEL, trust=True)
    await ops_test.model.wait_for_idle(
        apps=[INGRESS_NAME],
        status="waiting",
        raise_on_blocked=False,
        timeout=200,
    )
    await ops_test.model.integrate(APP_NAME, INGRESS_NAME)
    await ops_test.model.wait_for_idle(
        apps=[INGRESS_NAME],
        status="active",
        raise_on_blocked=False,
        timeout=300,
    )


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


async def ensure_model(
    label: str, ops_test: OpsTest, cloud_name: str, cloud_type: Literal["k8s", "lxd"], enable_debug_logs: bool = True
) -> str:
    """Get (or create) the model on the given cloud and return its name.

    Args:
        label: Unique label to ensure a distinct model across tests.
        ops_test: PyTest object.
        cloud_name: Name of the cloud from the Juju controller.
        cloud_type: Type of the cloud between k8s and lxd.
        enable_debug_logs: Whether to have debug logs enabled in the model.

    Returns:
        Name of the model being ensured.

    Raises:
        SetupFailedError: If model creation or configuration fails.
    """
    # Ref: https://github.com/canonical/livepatch-k8s-operator/blob/b48995d02f549dca0be46dd15790b5985669d7fe/tests/integration/helpers.py#L32  # noqa: W505
    model_name = f"datahub-test-{label}-{cloud_name}-{__RUN_ID}"
    # Juju limits model names to 63 characters. This does not guarantee name uniqueness!
    model_name = model_name[:63]
    if any((v.model_name == model_name for v in ops_test.models.values())):
        return model_name

    # Explicitly create the model to avoid auth errors.
    code, stdout, stderr = await ops_test.juju("add-model", model_name, cloud_name)
    if code != 0:
        logger.error("'juju add-model' failed:\n\tstdout: %s\n\tstderr: %s", stdout, stderr)
        raise exceptions.SetupFailedError("command 'juju add-model' failed")

    model = await ops_test.track_model(
        alias=model_name,
        model_name=model_name,
        cloud_name=cloud_name,
        use_existing=True,
        keep=True,  # TODO (mertalpt): Switched for debug purposed, rollback before merge.
    )

    # No need for further processing.
    if cloud_type != "k8s":
        return model.name

    # There is a bug that prevents deploying charms for k8s cloud on lxd controller.
    # Prevent by updating the workload storage parameter.
    # See:
    # - https://bugs.launchpad.net/juju/+bug/2031216
    # - https://bugs.launchpad.net/juju/+bug/2077426
    controller_cloud = await (await model.get_controller()).cloud()
    if controller_cloud.cloud.type_ == "lxd":
        code, stdout, stderr = await ops_test.juju(
            "model-config", "-m", model.name, "workload-storage=microk8s-hostpath"
        )
        if code != 0:
            logger.error("'juju model-config' failed:\n\tstdout: %s\n\tstderr: %s", stdout, stderr)
            raise exceptions.SetupFailedError("command 'juju model-config' failed")

    if enable_debug_logs:
        code, stdout, stderr = await ops_test.juju(
            "model-config", "-m", model.name, "logging-config=<root>=INFO;unit=DEBUG"
        )
        if code != 0:
            logger.warning("could not enable debug logs, proceeding\n\tstdout: %s\n\tstderr: %s", stdout, stderr)

    return model.name
