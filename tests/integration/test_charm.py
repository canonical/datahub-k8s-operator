#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Run integration tests."""

import logging
import time
from pathlib import Path

import helpers
import jubilant
import pytest
import requests

logger = logging.getLogger(__name__)


def _get_password(juju: jubilant.Juju) -> str:
    """Get admin password via the get-password action."""
    return helpers.get_admin_password(juju)


@pytest.fixture(scope="module")
def full_stack(k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: Path, rock_resources: dict) -> jubilant.Juju:
    """Deploy full dependency stack and return deployed K8s model handle."""
    logger.info("Deploying '%s'", helpers.APP_NAME)
    helpers.deploy_charm(k8s_juju, charm, rock_resources)

    logger.info("Deploying LXD dependencies")
    helpers.deploy_lxd_dependencies(lxd_juju)

    logger.info("Consuming offers and integrating DataHub")
    helpers.consume_and_integrate(k8s_juju, lxd_juju)

    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME], timeout=30 * 60)

    return k8s_juju


def test_build_and_deploy_solo(solo_juju: jubilant.Juju, charm: Path, rock_resources: dict):
    """Deploy the charm by itself and assert expected blocked status."""
    helpers.deploy_charm(solo_juju, charm, rock_resources)
    helpers.wait_for_apps_status(
        solo_juju,
        {
            helpers.APP_NAME: "blocked",
        },
        timeout=20 * 60,
        raise_on_error=False,
    )


def test_deploy_full(full_stack: jubilant.Juju):
    """Verify full stack deployment by authenticating to the frontend."""
    logger.info("Building unit URL")
    base_url = helpers.get_unit_url(full_stack, helpers.APP_NAME, 0, 9002)

    logger.info("Fetching admin password")
    admin_pwd = _get_password(full_stack)

    url = f"{base_url}/logIn"
    login_payload = {"username": "datahub", "password": admin_pwd}
    logger.info("Testing against URL: %s", url)

    logger.info("Waiting for frontend to accept an admin login")
    response = helpers.request_until(None, "POST", url, json=login_payload)
    assert response.status_code == 200, f"login never succeeded: {response.status_code} {response.text[:500]}"

    logger.info("Verifying GMS workload version is embedded")
    gms_url = helpers.get_unit_url(full_stack, helpers.APP_NAME, 0, 8080)
    config_response = helpers.request_until(None, "GET", f"{gms_url}/config")
    assert config_response.status_code == 200
    versions = config_response.json().get("versions", {})
    datahub_info = versions.get("acryldata/datahub", {})
    datahub_version = datahub_info.get("version")
    datahub_commit = datahub_info.get("commit")

    logger.info("DATAHUB_GMS_VERSION version=%s commit=%s", datahub_version, datahub_commit)
    assert (
        datahub_version and datahub_version != "null"
    ), f"DataHub /config returned invalid version: {datahub_version!r}"

    # Exercise the OpenSearch-backed search path. If the JVM truststore is
    # missing the OpenSearch CA, this surfaces as PKIX errors in the GraphQL
    # response.
    logger.info("Verifying GMS can serve search queries (OpenSearch TLS path)")
    session = requests.Session()
    login_response = helpers.request_until(session, "POST", url, json=login_payload)
    assert login_response.status_code == 200

    graphql_url = f"{base_url}/api/v2/graphql"
    query = {"query": ('{ searchAcrossEntities(input: {types: [DATASET], query: "*", count: 1})' " { total } }")}

    errors: list = []
    ssl_errors: list = []
    search_total = None
    body: dict = {}
    for _ in range(30):
        search_response = helpers.request_until(session, "POST", graphql_url, json=query)
        assert (
            search_response.status_code == 200
        ), f"GraphQL request returned {search_response.status_code}: {search_response.text[:500]}"
        body = search_response.json()
        errors = body.get("errors") or []
        search_total = body.get("data", {}).get("searchAcrossEntities", {}).get("total")
        ssl_errors = [e for e in errors if any(token in str(e) for token in ("PKIX", "SSL", "version='unknown'"))]
        if not errors and search_total is not None:
            break
        time.sleep(10)

    logger.info(
        "DATAHUB_GMS_SEARCH errors=%d ssl_errors=%d total_present=%s",
        len(errors),
        len(ssl_errors),
        search_total is not None,
    )
    assert not ssl_errors, f"SSL/trust errors during search: {ssl_errors}"
    assert not errors, f"GraphQL search returned errors: {errors}"
    assert search_total is not None, f"GraphQL search did not return data.searchAcrossEntities.total: {body}"


def test_reindex_action(full_stack: jubilant.Juju):
    """Run and verify the reindex action."""
    logger.info("Testing reindex action")
    action_output = full_stack.run(f"{helpers.APP_NAME}/0", "reindex", {"clean": True}, wait=30 * 60)
    logger.info("Action result: %s", action_output.results)

    assert "result" in action_output.results
    assert "command succeeded" in action_output.results["result"]


def test_ingress_routes_traffic(full_stack: jubilant.Juju):
    """Verify each Traefik publishes a URL that actually reaches its requirer endpoint."""
    juju = full_stack

    fe_url = helpers.get_first_proxied_url(juju, helpers.INGRESS_FRONTEND_NAME)
    gms_url = helpers.get_first_proxied_url(juju, helpers.INGRESS_GMS_NAME)
    logger.info("Frontend ingress URL: %s", fe_url)
    logger.info("GMS ingress URL: %s", gms_url)

    # Frontend serves an HTML SPA at the root.
    fe_response = requests.get(fe_url, timeout=30, allow_redirects=False)
    assert (
        200 <= fe_response.status_code < 400
    ), f"Expected 2xx/3xx from frontend ingress URL, got {fe_response.status_code}: {fe_response.text[:200]}"

    # GMS serves /config as JSON including a "versions" key.
    gms_response = requests.get(f"{gms_url.rstrip('/')}/config", timeout=30)
    assert (
        gms_response.status_code == 200
    ), f"Expected 200 from GMS /config, got {gms_response.status_code}: {gms_response.text[:200]}"
    body = gms_response.json()
    assert "versions" in body, f"GMS /config missing 'versions' key: {body}"


def test_oauth_blocks_without_https_then_recovers(full_stack: jubilant.Juju):
    """An oauth relation without HTTPS ingress blocks; integrating TLS lets the charm recover."""
    juju = full_stack
    app = helpers.APP_NAME

    logger.info("Deploying oauth-external-idp-integrator with stub IdP config")
    helpers.deploy_oauth_integrator(juju)
    juju.integrate(f"{app}:oauth", f"{helpers.OAUTH_INTEGRATOR_NAME}:oauth")
    helpers.wait_for_apps_status(
        juju,
        {helpers.OAUTH_INTEGRATOR_NAME: "active"},
        timeout=10 * 60,
    )

    try:
        logger.info("Waiting for charm to block on OIDC HTTPS requirement")

        def _is_blocked_on_oidc(status: jubilant.Status) -> bool:
            """Return True when the charm is blocked with the OIDC HTTPS message."""
            app_status = status.apps.get(app)
            if not app_status:
                return False
            msg = app_status.app_status.message or ""
            return app_status.app_status.current == "blocked" and "OIDC requires an HTTPS" in msg

        juju.wait(_is_blocked_on_oidc, timeout=5 * 60, delay=10, successes=1)

        logger.info("Deploying self-signed-certificates and integrating with both traefiks")
        juju.deploy(helpers.K8S_CERTIFICATES_NAME, channel=helpers.K8S_CERTIFICATES_CHANNEL)
        helpers.wait_for_apps_status(
            juju,
            {helpers.K8S_CERTIFICATES_NAME: "active"},
            timeout=10 * 60,
        )
        for traefik in helpers.INGRESS_APPS:
            juju.integrate(
                f"{traefik}:certificates",
                f"{helpers.K8S_CERTIFICATES_NAME}:certificates",
            )
        helpers.wait_for_all_active(
            juju,
            [*helpers.INGRESS_APPS, helpers.K8S_CERTIFICATES_NAME, app],
            timeout=15 * 60,
        )

        logger.info("Verifying both traefiks now publish HTTPS URLs")
        for traefik in helpers.INGRESS_APPS:
            url = helpers.get_first_proxied_url(juju, traefik)
            assert url.startswith("https://"), f"{traefik} URL still plain http: {url}"

        logger.info("Verifying the frontend redirects /sso to the IdP authorization endpoint")
        fe_url = helpers.get_first_proxied_url(juju, helpers.INGRESS_FRONTEND_NAME)
        sso_url = f"{fe_url.rstrip('/')}/sso"
        location = ""
        for _ in range(30):
            response = requests.get(sso_url, timeout=30, verify=False, allow_redirects=False)  # nosec B501
            location = response.headers.get("Location", "")
            if response.status_code in (302, 303) and "accounts.google.com" in location:
                break
            time.sleep(10)
        assert "accounts.google.com" in location, f"/sso did not redirect to the IdP: {location!r}"

    finally:
        logger.info("Tearing down the oauth relation and TLS integrations so the model stays reusable")
        try:
            juju.remove_relation(f"{app}:oauth", f"{helpers.OAUTH_INTEGRATOR_NAME}:oauth")
        except jubilant.CLIError as exc:
            logger.warning("Failed to remove oauth relation: %s", exc)
        for traefik in helpers.INGRESS_APPS:
            try:
                juju.remove_relation(
                    f"{traefik}:certificates",
                    f"{helpers.K8S_CERTIFICATES_NAME}:certificates",
                )
            except jubilant.CLIError as exc:
                logger.warning("Failed to remove TLS relation for %s: %s", traefik, exc)
        helpers.wait_for_apps_status(
            juju,
            {app: "active"},
            timeout=10 * 60,
            raise_on_error=False,
        )
