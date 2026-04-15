# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import logging
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence, Tuple

import jubilant
import yaml

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]

KAFKA_NAME = "kafka"
KAFKA_CHANNEL = "3/stable"
KAFKA_REVISION = 195
KAFKA_OFFER_NAME = "kafka-client"
OPENSEARCH_NAME = "opensearch"
OPENSEARCH_CHANNEL = "2/stable"
OPENSEARCH_REVISION = 168
OPENSEARCH_OFFER_NAME = "os-client"
POSTGRES_NAME = "postgresql"
POSTGRES_CHANNEL = "14/stable"
POSTGRES_REVISION = 553
POSTGRES_OFFER_NAME = "pg-client"
CERTIFICATES_NAME = "self-signed-certificates"
CERTIFICATES_CHANNEL = "latest/stable"
CERTIFICATES_REVISION = 264
ZOOKEPER_NAME = "zookeeper"
ZOOKEEPER_CHANNEL = "3/stable"
ZOOKEEPER_REVISION = 149
INGRESS_NAME = "nginx-ingress-integrator"
INGRESS_CHANNEL = "stable"


def _app_status_current(status: jubilant.Status, app_name: str) -> str:
    """Read workload status for an app, returning empty string if app is absent."""
    app = status.apps.get(app_name)
    if not app:
        return ""
    return app.app_status.current


def wait_for_apps_status(
    juju: jubilant.Juju,
    expected_by_app: Mapping[str, str | Sequence[str]],
    *,
    timeout: float,
    raise_on_error: bool = True,
) -> None:
    """Wait until all apps in expected_by_app reach one of the expected workload statuses.

    Args:
        juju: Jubilant object.
        expected_by_app: Mapping of app name to expected status string(s).
        timeout: Maximum seconds to wait.
        raise_on_error: If True, raise on any app entering error status.
    """
    normalized: Dict[str, set[str]] = {}
    for app, expected in expected_by_app.items():
        if isinstance(expected, str):
            normalized[app] = {expected}
        else:
            normalized[app] = set(expected)

    wait_kwargs: dict = {"timeout": timeout}
    if raise_on_error:
        wait_kwargs["error"] = lambda status: jubilant.any_error(status, *normalized.keys())
    juju.wait(
        lambda status: all(_app_status_current(status, app) in wanted for app, wanted in normalized.items()),
        **wait_kwargs,
    )


def wait_for_all_active(juju: jubilant.Juju, apps: Iterable[str], *, timeout: float) -> None:
    """Wait until all applications and units for apps are active.

    Args:
        juju: Jubilant object.
        apps: Application names to wait for.
        timeout: Maximum seconds to wait.
    """
    app_list = tuple(apps)
    juju.wait(
        lambda status: jubilant.all_active(status, *app_list),
        error=lambda status: jubilant.any_error(status, *app_list),
        timeout=timeout,
    )


def add_juju_secrets(juju: jubilant.Juju) -> Dict[str, Tuple[str, str]]:
    """Add required Juju user secrets to model.

    Args:
        juju: Jubilant object.

    Returns:
        Dictionary of secrets to IDs.
    """
    keys_name = "encryption_keys"
    secrets = juju.secrets()
    current_secret = next(
        (secret for secret in secrets if secret.label == keys_name or secret.name == keys_name),
        None,
    )
    if current_secret:
        keys_uri = str(current_secret.uri)
    else:
        keys_uri = str(
            juju.add_secret(
                name=keys_name,
                content={"gms-key": "secret_secret123", "frontend-key": "secret_secret123"},
            )
        )

    ret = {
        "encryption-keys-secret": (keys_uri.split(":")[-1], keys_uri),
    }
    return ret


def deploy_charm(juju: jubilant.Juju, charm: Path, resources: dict) -> None:
    """Deploy the charm with the given config and OCI resources.

    Args:
        juju: Jubilant object.
        charm: Path to charm package.
        resources: Dictionary linking the resource name to mapped local registry image.
    """
    secrets = add_juju_secrets(juju)
    config = {"encryption-keys-secret-id": secrets["encryption-keys-secret"][0]}

    juju.deploy(charm, app=APP_NAME, resources=resources, config=config)
    juju.grant_secret(secrets["encryption-keys-secret"][1], APP_NAME)

    # Setup the ingress.
    juju.deploy(INGRESS_NAME, channel=INGRESS_CHANNEL, trust=True)
    wait_for_apps_status(
        juju,
        {
            INGRESS_NAME: ("waiting", "active"),
        },
        timeout=5 * 60,
        raise_on_error=False,
    )
    juju.integrate(f"{APP_NAME}:nginx-fe-route", INGRESS_NAME)
    wait_for_apps_status(
        juju,
        {
            INGRESS_NAME: "active",
        },
        timeout=10 * 60,
    )


def get_unit_url(juju: jubilant.Juju, application: str, unit: int, port: int, protocol: str = "http") -> str:
    """Return unit URL from the model.

    Args:
        juju: Jubilant object.
        application: Name of the application.
        unit: Number of the unit.
        port: Port number of the URL.
        protocol: Transfer protocol (default: http).

    Returns:
        Unit URL of the form {protocol}://{address}:{port}

    Raises:
        ValueError: If no reachable address is found for the unit.
    """
    status = juju.status()
    unit_name = f"{application}/{unit}"
    app_status = status.apps[application]
    unit_status = app_status.units.get(unit_name)

    address = ""
    if unit_status:
        address = unit_status.public_address or unit_status.address
    if not address:
        address = app_status.address
    if not address:
        raise ValueError(f"No reachable address found for unit '{unit_name}'")

    return f"{protocol}://{address}:{port}"


def model_short_name(model_name: str) -> str:
    """Return model name without controller prefix.

    Args:
        model_name: Full model name, possibly controller-prefixed.

    Returns:
        Model name without the controller prefix.
    """
    if ":" in model_name:
        return model_name.split(":", maxsplit=1)[1]
    return model_name
