# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Helpers for integration tests."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import jubilant
import requests
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
INGRESS_CHARM = "traefik-k8s"
INGRESS_CHANNEL = "latest/stable"
INGRESS_FRONTEND_NAME = "traefik-frontend"
INGRESS_GMS_NAME = "traefik-gms"
INGRESS_APPS = (INGRESS_FRONTEND_NAME, INGRESS_GMS_NAME)
K8S_CERTIFICATES_NAME = "self-signed-certificates"
K8S_CERTIFICATES_CHANNEL = "latest/stable"


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
        Dictionary mapping secret names to ``(secret_id, secret_uri)`` tuples.
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


def deploy_charm(
    juju: jubilant.Juju,
    charm: Optional[Path] = None,
    resources: Optional[dict] = None,
    *,
    channel: Optional[str] = None,
) -> None:
    """Deploy the charm, either from a local package or from a Charmhub channel.

    Passing ``channel`` deploys the published charm (with its store resources)
    and is used by the upgrade regression test as the "old" version. Otherwise
    a local ``charm`` package plus local ``resources`` are deployed. In both
    cases the encryption-keys secret, its grant, and the two Traefik ingresses
    are wired up identically.

    Args:
        juju: Jubilant object.
        charm: Path to charm package (required unless ``channel`` is given).
        resources: Resource-name to local registry image map (required unless
            ``channel`` is given).
        channel: Charmhub channel to deploy from instead of a local package.

    Raises:
        ValueError: If neither ``channel`` nor both ``charm`` and ``resources``
            are provided.
    """
    secrets = add_juju_secrets(juju)
    config = {"encryption-keys-secret-id": secrets["encryption-keys-secret"][0]}

    if channel:
        juju.deploy(APP_NAME, app=APP_NAME, channel=channel, config=config)
    elif charm is not None and resources is not None:
        juju.deploy(charm, app=APP_NAME, resources=resources, config=config)
    else:
        raise ValueError("deploy_charm requires either channel= or both charm and resources")
    juju.grant_secret(secrets["encryption-keys-secret"][1], APP_NAME)

    # Setup the ingress.
    for traefik_app, requirer_endpoint in (
        (INGRESS_FRONTEND_NAME, "frontend-ingress"),
        (INGRESS_GMS_NAME, "gms-ingress"),
    ):
        juju.deploy(
            INGRESS_CHARM,
            app=traefik_app,
            channel=INGRESS_CHANNEL,
            trust=True,
        )
        juju.integrate(f"{APP_NAME}:{requirer_endpoint}", traefik_app)

    wait_for_apps_status(
        juju,
        {name: "active" for name in INGRESS_APPS},
        timeout=10 * 60,
    )


def deploy_lxd_dependencies(lxd_juju: jubilant.Juju) -> None:
    """Deploy DataHub's machine dependencies on LXD, settle them, and create offers.

    Deploys Kafka, OpenSearch, PostgreSQL, Zookeeper and self-signed-certificates,
    integrates them, waits for all to go active, then offers the three client
    endpoints DataHub consumes. Shared by every integration module that needs a
    full stack.

    Args:
        lxd_juju: Jubilant object for the LXD model.
    """
    lxd_juju.deploy(KAFKA_NAME, channel=KAFKA_CHANNEL, revision=KAFKA_REVISION)
    lxd_juju.deploy(OPENSEARCH_NAME, channel=OPENSEARCH_CHANNEL, num_units=2, revision=OPENSEARCH_REVISION)
    lxd_juju.deploy(POSTGRES_NAME, channel=POSTGRES_CHANNEL, revision=POSTGRES_REVISION)
    lxd_juju.deploy(CERTIFICATES_NAME, channel=CERTIFICATES_CHANNEL, revision=CERTIFICATES_REVISION)
    lxd_juju.deploy(ZOOKEPER_NAME, channel=ZOOKEEPER_CHANNEL, revision=ZOOKEEPER_REVISION)

    logger.info("Waiting for LXD dependencies to settle")
    wait_for_apps_status(
        lxd_juju,
        {
            POSTGRES_NAME: "active",
            CERTIFICATES_NAME: "active",
            ZOOKEPER_NAME: "active",
            KAFKA_NAME: ("blocked", "waiting"),
            OPENSEARCH_NAME: ("blocked", "waiting"),
        },
        timeout=30 * 60,
        raise_on_error=False,
    )

    logger.info("Integrating LXD dependencies")
    lxd_juju.integrate(KAFKA_NAME, ZOOKEPER_NAME)
    lxd_juju.integrate(OPENSEARCH_NAME, CERTIFICATES_NAME)
    wait_for_all_active(
        lxd_juju,
        [KAFKA_NAME, OPENSEARCH_NAME, POSTGRES_NAME, CERTIFICATES_NAME, ZOOKEPER_NAME],
        timeout=20 * 60,
    )

    logger.info("Creating offers")
    lxd_juju.offer(KAFKA_NAME, endpoint="kafka-client", name=KAFKA_OFFER_NAME)
    lxd_juju.offer(OPENSEARCH_NAME, endpoint="opensearch-client", name=OPENSEARCH_OFFER_NAME)
    lxd_juju.offer(POSTGRES_NAME, endpoint="database", name=POSTGRES_OFFER_NAME)


def consume_and_integrate(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju) -> None:
    """Consume the LXD dependency offers into the K8s model and integrate DataHub.

    Must be called after :func:`deploy_lxd_dependencies` (offers exist) and after
    the DataHub charm is deployed in ``k8s_juju``. Leaves the caller to wait for
    DataHub to reach active.

    Args:
        k8s_juju: Jubilant object for the K8s model (DataHub deployed).
        lxd_juju: Jubilant object for the LXD model (offers created).

    Raises:
        ValueError: If the LXD model name cannot be determined.
    """
    lxd_model = model_short_name(lxd_juju.model or "")
    if not lxd_model:
        raise ValueError("Could not determine LXD model name")

    logger.info("Consuming offers")
    k8s_juju.consume(f"{lxd_model}.{KAFKA_OFFER_NAME}")
    k8s_juju.consume(f"{lxd_model}.{OPENSEARCH_OFFER_NAME}")
    k8s_juju.consume(f"{lxd_model}.{POSTGRES_OFFER_NAME}")

    logger.info("Waiting for K8s applications to settle")
    wait_for_apps_status(
        k8s_juju,
        {APP_NAME: ("blocked", "waiting", "maintenance", "active")},
        timeout=20 * 60,
        raise_on_error=False,
    )

    logger.info("Integrating consumed offers")
    k8s_juju.integrate(f"{APP_NAME}:kafka", KAFKA_OFFER_NAME)
    k8s_juju.integrate(f"{APP_NAME}:opensearch", OPENSEARCH_OFFER_NAME)
    k8s_juju.integrate(f"{APP_NAME}:db", POSTGRES_OFFER_NAME)


def get_admin_password(juju: jubilant.Juju, unit: int = 0) -> str:
    """Return the auto-generated admin password via the get-password action.

    Args:
        juju: Jubilant object.
        unit: Unit number to run the action on (default 0).

    Returns:
        The admin password string.

    Raises:
        ValueError: If the action does not return a password.
    """
    action_output = juju.run(f"{APP_NAME}/{unit}", "get-password", wait=60)
    password = action_output.results.get("password", "")
    if not password:
        raise ValueError("get-password action did not return a password")
    return password


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


def request_until(
    session: Optional[requests.Session],
    method: str,
    url: str,
    *,
    expected_status: int = 200,
    attempts: int = 60,
    delay: float = 10.0,
    timeout: float = 15.0,
    **kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request, retrying until it returns ``expected_status``.

    Args:
        session: A requests Session (to carry cookies across calls) or None for
            a plain stateless request.
        method: HTTP method, e.g. "GET" or "POST".
        url: Target URL.
        expected_status: Status code to wait for (default 200).
        attempts: Maximum number of attempts (default 60).
        delay: Seconds to sleep between attempts (default 10).
        timeout: Per-request timeout in seconds (default 15).
        kwargs: Forwarded to requests (e.g. ``json=``).

    Returns:
        The first response with ``expected_status``, or the last one seen.

    Raises:
        AssertionError: If every attempt raised a connection error.
    """
    requester = session.request if session is not None else requests.request
    last_response: Optional[requests.Response] = None
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            response = requester(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            logger.info("%s %s failed: %s (attempt %d/%d)", method, url, exc, attempt, attempts)
        else:
            if response.status_code == expected_status:
                return response
            last_response = response
            logger.info(
                "%s %s -> %d, want %d (attempt %d/%d); body=%s",
                method,
                url,
                response.status_code,
                expected_status,
                attempt,
                attempts,
                response.text[:300],
            )
        time.sleep(delay)
    if last_response is not None:
        return last_response
    raise AssertionError(f"{method} {url} never returned a response: {last_error}")


def get_proxied_endpoints(juju: jubilant.Juju, traefik_app: str) -> Dict[str, Any]:
    """Return the proxied endpoints exposed by ``<traefik_app>/0``.

    Args:
        juju: Jubilant object pointing at the K8s model where Traefik is deployed.
        traefik_app: Application name of the Traefik instance to query
            (e.g. ``traefik-frontend`` or ``traefik-gms``).

    Returns:
        The parsed ``proxied-endpoints`` mapping returned by Traefik's
        ``show-proxied-endpoints`` action. Keys are ingress requirer
        identifiers; each value is a dict with at least a ``url`` field.
    """
    action_output = juju.run(f"{traefik_app}/0", "show-proxied-endpoints", wait=60)
    raw = action_output.results.get("proxied-endpoints", "{}")
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def get_first_proxied_url(juju: jubilant.Juju, traefik_app: str) -> str:
    """Return the first URL exposed by a Traefik app's ``show-proxied-endpoints``.

    Args:
        juju: Jubilant object.
        traefik_app: Traefik app name.

    Returns:
        The first URL found that belongs to a requirer (i.e. excludes the
        traefik-itself entry).

    Raises:
        ValueError: If no requirer URL is found.
    """
    endpoints = get_proxied_endpoints(juju, traefik_app)
    for key, value in endpoints.items():
        if key == traefik_app:
            continue
        if isinstance(value, dict) and "url" in value:
            return value["url"]
    raise ValueError(f"No requirer URL found in {traefik_app} proxied-endpoints: {endpoints}")


def add_oidc_secret(juju: jubilant.Juju) -> Tuple[str, str]:
    """Add a stub OIDC client-credentials Juju secret to the K8s model.

    The content is not validated against a real IdP — the secret only exists
    so the charm's ``_check_state`` exercises the OIDC code paths.

    Args:
        juju: Jubilant object pointing at the K8s model.

    Returns:
        ``(secret_id, secret_uri)`` tuple.
    """
    name = "oidc_keys"
    existing = next(
        (s for s in juju.secrets() if s.label == name or s.name == name),
        None,
    )
    if existing:
        uri = str(existing.uri)
    else:
        uri = str(
            juju.add_secret(
                name=name,
                content={"client-id": "stub-client-id", "client-secret": "stub-client-secret"},
            )
        )
    return uri.split(":")[-1], uri


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
