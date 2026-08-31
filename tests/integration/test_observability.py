#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the observability endpoints."""

import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import helpers
import jubilant
import pytest
import requests

logger = logging.getLogger(__name__)

SCRAPE_JOBS = ("datahub-gms", "datahub-frontend")

ALERT_RULES = (
    "DatahubServiceDown",
    "DatahubHeapStarvation",
    "DatahubExcessiveGC",
    "DatahubKafkaConsumerLag",
)

DASHBOARD_TITLE = "DataHub Monitoring"

PEBBLE_SERVICES = ("datahub-gms", "datahub-frontend", "datahub-actions")

LOG_LOOKBACK_SECONDS = 60 * 60


@pytest.fixture(scope="module")
def observed_stack(
    k8s_juju: jubilant.Juju, lxd_juju: jubilant.Juju, charm: Path, rock_resources: dict
) -> jubilant.Juju:
    """Deploy DataHub with its full dependency stack, related to Prometheus, Grafana and Loki."""
    logger.info("Deploying '%s'", helpers.APP_NAME)
    helpers.deploy_charm(k8s_juju, charm, rock_resources)

    logger.info("Deploying LXD dependencies")
    helpers.deploy_lxd_dependencies(lxd_juju)

    logger.info("Consuming offers and integrating DataHub")
    helpers.consume_and_integrate(k8s_juju, lxd_juju)

    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME], timeout=30 * 60)

    logger.info("Deploying Prometheus, Loki and Grafana")
    helpers.deploy_cos(k8s_juju)

    logger.info("Integrating DataHub's observability endpoints")
    k8s_juju.integrate(f"{helpers.APP_NAME}:metrics-endpoint", f"{helpers.PROMETHEUS_NAME}:metrics-endpoint")
    k8s_juju.integrate(f"{helpers.APP_NAME}:grafana-dashboard", f"{helpers.GRAFANA_NAME}:grafana-dashboard")
    k8s_juju.integrate(f"{helpers.APP_NAME}:logging", f"{helpers.LOKI_NAME}:logging")
    helpers.wait_for_all_active(k8s_juju, [helpers.APP_NAME, *helpers.COS_APPS], timeout=15 * 60)

    return k8s_juju


def _prometheus_url(juju: jubilant.Juju) -> str:
    """Return the base URL of the Prometheus API.

    Args:
        juju: Jubilant object.

    Returns:
        The Prometheus base URL.
    """
    return helpers.get_unit_url(juju, helpers.PROMETHEUS_NAME, 0, helpers.PROMETHEUS_PORT)


def _prometheus_query(juju: jubilant.Juju, query: str) -> List[Dict[str, Any]]:
    """Run an instant query against Prometheus and return its result vector.

    Args:
        juju: Jubilant object.
        query: PromQL query.

    Returns:
        The list of samples in the result vector.
    """
    response = requests.get(f"{_prometheus_url(juju)}/api/v1/query", params={"query": query}, timeout=30)
    response.raise_for_status()
    return response.json()["data"]["result"]


def _up_series(juju: jubilant.Juju) -> Dict[str, str]:
    """Return the `up` sample value of each DataHub scrape job, keyed by job name.

    Prometheus prefixes the job label with Juju topology, so the charm's own job
    names are matched as a suffix.

    Args:
        juju: Jubilant object.

    Returns:
        Mapping of the charm's scrape job names to their `up` values.
    """
    samples = _prometheus_query(juju, f'up{{juju_application="{helpers.APP_NAME}"}}')
    series = {}
    for sample in samples:
        job = sample["metric"].get("job", "")
        for name in SCRAPE_JOBS:
            if name in job:
                series[name] = sample["value"][1]
    return series


def _loki_streams(juju: jubilant.Juju, query: str) -> List[Dict[str, Any]]:
    """Run a LogQL range query against Loki and return the streams it matched.

    Args:
        juju: Jubilant object.
        query: LogQL selector.

    Returns:
        The list of matched streams.
    """
    loki_url = helpers.get_unit_url(juju, helpers.LOKI_NAME, 0, helpers.LOKI_PORT)
    now = time.time_ns()
    response = requests.get(
        f"{loki_url}/loki/api/v1/query_range",
        params={
            "query": query,
            "start": str(now - LOG_LOOKBACK_SECONDS * 1_000_000_000),
            "end": str(now),
            "limit": "5",
            "direction": "backward",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"]["result"]


def test_metrics_endpoint_scrapes_both_exporters(observed_stack: jubilant.Juju):
    """Prometheus scrapes the GMS and frontend JMX exporters, and both are up."""
    juju = observed_stack

    helpers.poll_until(
        juju,
        lambda: set(_up_series(juju)) == set(SCRAPE_JOBS),
        f"Prometheus never scraped both DataHub jobs {SCRAPE_JOBS}",
    )

    series = _up_series(juju)
    logger.info("DATAHUB_SCRAPE_TARGETS %s", series)
    for job, value in series.items():
        assert value == "1", f"scrape job '{job}' is down"


def test_metrics_endpoint_ships_the_alert_rules(observed_stack: jubilant.Juju):
    """The alert rules the charm ships load into Prometheus over the same relation."""
    juju = observed_stack

    def _loaded_rules() -> set:
        """Return the names of every alert rule Prometheus has loaded."""
        response = requests.get(f"{_prometheus_url(juju)}/api/v1/rules", timeout=30)
        response.raise_for_status()
        groups = response.json()["data"]["groups"]
        return {rule["name"] for group in groups for rule in group.get("rules", [])}

    helpers.poll_until(
        juju,
        lambda: set(ALERT_RULES) <= _loaded_rules(),
        f"Prometheus never loaded the DataHub alert rules {ALERT_RULES}",
    )


def test_grafana_dashboard_is_published(observed_stack: jubilant.Juju):
    """The dashboard the charm ships shows up in Grafana."""
    juju = observed_stack

    grafana_url = helpers.get_unit_url(juju, helpers.GRAFANA_NAME, 0, helpers.GRAFANA_PORT)
    password = helpers.get_grafana_admin_password(juju)

    def _dashboard_titles() -> set:
        """Return the titles of the dashboards Grafana knows about."""
        response = requests.get(
            f"{grafana_url}/api/search",
            params={"type": "dash-db"},
            auth=("admin", password),
            timeout=30,
        )
        response.raise_for_status()
        return {entry.get("title", "") for entry in response.json()}

    helpers.poll_until(
        juju,
        lambda: any(DASHBOARD_TITLE in title for title in _dashboard_titles()),
        f"Grafana never loaded a '{DASHBOARD_TITLE}' dashboard",
    )


def test_logging_forwards_every_container(observed_stack: jubilant.Juju):
    """Loki receives log lines from all three DataHub containers."""
    juju = observed_stack

    for service in PEBBLE_SERVICES:
        query = f'{{juju_application="{helpers.APP_NAME}", pebble_service="{service}"}}'
        logger.info("Waiting for Loki to receive logs matching %s", query)
        helpers.poll_until(
            juju,
            lambda q=query: bool(_loki_streams(juju, q)),
            f"Loki never received logs from the '{service}' container",
        )
