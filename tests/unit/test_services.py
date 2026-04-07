# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for services.py."""

import os
from types import SimpleNamespace
from unittest.mock import patch

import services


def test_compile_standard_proxy_environment():
    """Compile standard proxy variables from Juju proxy environment."""
    with patch.dict(
        os.environ,
        {
            "JUJU_CHARM_HTTP_PROXY": "http://proxy.example:8080",
            "JUJU_CHARM_HTTPS_PROXY": "http://proxy.example:8443",
            "JUJU_CHARM_NO_PROXY": "10.0.0.1,svc.cluster.local",
        },
        clear=True,
    ):
        env = services._compile_standard_proxy_environment(
            extra_no_proxy_hosts=["localhost", "localhost", "datahub-gms"]
        )

    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["http_proxy"] == "http://proxy.example:8080"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8443"
    assert env["https_proxy"] == "http://proxy.example:8443"
    assert env["NO_PROXY"] == "10.0.0.1,svc.cluster.local,localhost,datahub-gms"
    assert env["no_proxy"] == "10.0.0.1,svc.cluster.local,localhost,datahub-gms"


def test_actions_compile_environment_includes_proxy_vars():
    """Actions environment should include standard proxy variables."""
    state = SimpleNamespace(
        kafka_connection={
            "bootstrap_server": "kafka:9092",
            "username": "user",
            "password": "pass",  # nosec
        }
    )
    config = SimpleNamespace(kafka_topic_prefix="")
    context = services.ServiceContext(charm=SimpleNamespace(_state=state, config=config))

    with patch.object(services.ActionsService, "is_enabled", return_value=True):
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy.example:8080",
                "JUJU_CHARM_HTTPS_PROXY": "http://proxy.example:8443",
                "JUJU_CHARM_NO_PROXY": "10.0.0.1",
            },
            clear=True,
        ):
            env = services.ActionsService.compile_environment(context)

    assert env is not None
    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["http_proxy"] == "http://proxy.example:8080"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8443"
    assert env["https_proxy"] == "http://proxy.example:8443"
    assert env["NO_PROXY"] == "10.0.0.1,localhost"
    assert env["no_proxy"] == "10.0.0.1,localhost"


def test_actions_compile_environment_omits_proxy_vars_when_unset():
    """Actions environment should not include proxy variables when model proxy is unset."""
    state = SimpleNamespace(
        kafka_connection={
            "bootstrap_server": "kafka:9092",
            "username": "user",
            "password": "pass",  # nosec
        }
    )
    config = SimpleNamespace(kafka_topic_prefix="")
    context = services.ServiceContext(charm=SimpleNamespace(_state=state, config=config))

    with patch.object(services.ActionsService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            env = services.ActionsService.compile_environment(context)

    assert env is not None
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "NO_PROXY" not in env
    assert "http_proxy" not in env
    assert "https_proxy" not in env
    assert "no_proxy" not in env
