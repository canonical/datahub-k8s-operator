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


def test_gms_compile_environment_includes_system_client():
    """GMS environment should include DATAHUB_SYSTEM_CLIENT_ID/SECRET from trino_relation."""
    state = SimpleNamespace(
        database_connection={
            "host": "db",
            "port": "5432",
            "username": "u",
            "password": "p",  # nosec
            "dbname": "datahub_db",
            "initialized": True,
        },
        kafka_connection={
            "bootstrap_server": "kafka:9092",
            "username": "u",
            "password": "p",  # nosec
            "initialized": True,
        },
        opensearch_connection={
            "host": "os",
            "port": "9200",
            "username": "u",
            "password": "p",  # nosec
            "tls-ca": "cert",
            "initialized": True,
        },
        ran_upgrade=True,
        gms_truststore_initialized=True,
    )
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    model = SimpleNamespace(get_secret=lambda id: encryption_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
    )
    charm = SimpleNamespace(
        _state=state,
        config=config,
        model=model,
        system_client_id="__datahub_system",
        system_client_secret="my-secret",  # nosec B106
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.GMSService, "is_enabled", return_value=True):
        env = services.GMSService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == "__datahub_system"
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"
