# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for src/datahub_gc.py."""

import json
import os
from unittest.mock import patch

import pytest

import literals
from datahub_gc import reconcile_gc_source

PROXY_ENV = {
    "JUJU_CHARM_HTTP_PROXY": "http://proxy:8080",
    "JUJU_CHARM_HTTPS_PROXY": "http://proxy:8443",
    "JUJU_CHARM_NO_PROXY": "10.0.0.1",
}
PROXY_VARS = {
    "HTTP_PROXY": "http://proxy:8080",
    "http_proxy": "http://proxy:8080",
    "HTTPS_PROXY": "http://proxy:8443",
    "https_proxy": "http://proxy:8443",
    "NO_PROXY": "10.0.0.1,localhost",
    "no_proxy": "10.0.0.1,localhost",
}


def _bootstrapped_source(**config_overrides):
    """Return the source as GMS writes it from its bootstrap template."""
    config = {
        "recipe": '{"source": {"type": "datahub-gc", "config": {}}}',
        "executorId": "default",
        "version": "",
        "debugMode": False,
        "extraArgs": [],
    }
    config.update(config_overrides)
    return {
        "urn": literals.GC_INGESTION_SOURCE_URN,
        "name": "datahub-gc",
        "type": "datahub-gc",
        "config": config,
        "schedule": {"interval": "0 1 * * *", "timezone": "UTC"},
        "source": {"type": "SYSTEM"},
    }


def _env_vars(input_data):
    """Return the extra_env_vars sent in an update."""
    entries = [e for e in input_data["config"]["extraArgs"] if e["key"] == "extra_env_vars"]
    return json.loads(entries[0]["value"]) if entries else None


@pytest.fixture(name="update")
def update_fixture():
    """Patch the update mutation."""
    with patch("graphql.update_ingestion_source") as mock_update:
        yield mock_update


def _reconcile(source, env):
    """Run the reconcile against a fetched `source` with `env` as the process environment.

    Returns:
        What `reconcile_gc_source` returned.
    """
    with patch("graphql.get_ingestion_source", return_value=source) as mock_get:
        with patch.dict(os.environ, env, clear=True):
            result = reconcile_gc_source("client", "secret")
    mock_get.assert_called_once_with("client:secret", literals.GC_INGESTION_SOURCE_URN, auth_scheme="Basic")
    return result


def test_absent_source_is_left_alone(update):
    """Nothing is sent before GMS has created the source."""
    assert _reconcile(None, PROXY_ENV) is False
    update.assert_not_called()


def test_bootstrapped_source_gets_proxy_and_loses_empty_version(update):
    """The template's source gets the proxy and no version, and keeps everything else."""
    assert _reconcile(_bootstrapped_source(), PROXY_ENV) is True

    credentials, urn, input_data = update.call_args.args
    assert credentials == "client:secret"
    assert urn == literals.GC_INGESTION_SOURCE_URN
    assert update.call_args.kwargs == {"auth_scheme": "Basic"}
    assert _env_vars(input_data) == PROXY_VARS
    assert "version" not in input_data["config"]
    assert input_data["name"] == "datahub-gc"
    assert input_data["type"] == "datahub-gc"
    assert input_data["config"]["recipe"] == '{"source": {"type": "datahub-gc", "config": {}}}'
    assert input_data["config"]["executorId"] == "default"
    assert input_data["config"]["debugMode"] is False
    assert input_data["schedule"] == {"interval": "0 1 * * *", "timezone": "UTC"}
    assert input_data["source"] == {"type": "SYSTEM"}


def test_empty_version_is_removed_without_a_proxy(update):
    """A model without a proxy still gets the version fixed, and no env vars are added."""
    assert _reconcile(_bootstrapped_source(), {}) is True

    input_data = update.call_args.args[2]
    assert "version" not in input_data["config"]
    assert input_data["config"]["extraArgs"] == []


def test_current_source_is_not_updated(update):
    """A source that already has the proxy and no empty version is left alone."""
    extra_args = [{"key": "extra_env_vars", "value": json.dumps(PROXY_VARS)}]
    source = _bootstrapped_source(version=None, extraArgs=extra_args)

    assert _reconcile(source, PROXY_ENV) is False
    update.assert_not_called()


def test_operator_version_and_env_vars_are_kept(update):
    """A version and variables set by an operator survive the update."""
    extra_args = [{"key": "extra_env_vars", "value": json.dumps({"OPERATOR_VAR": "keep"})}]
    source = _bootstrapped_source(version="1.4.0.3", extraArgs=extra_args)

    assert _reconcile(source, PROXY_ENV) is True

    input_data = update.call_args.args[2]
    assert input_data["config"]["version"] == "1.4.0.3"
    assert _env_vars(input_data) == {"OPERATOR_VAR": "keep", **PROXY_VARS}


def test_proxy_removed_from_the_model_is_removed(update):
    """Proxy variables no longer in the model config are dropped."""
    extra_args = [{"key": "extra_env_vars", "value": json.dumps({**PROXY_VARS, "OPERATOR_VAR": "keep"})}]
    source = _bootstrapped_source(version=None, extraArgs=extra_args)

    assert _reconcile(source, {}) is True

    assert _env_vars(update.call_args.args[2]) == {"OPERATOR_VAR": "keep"}


def test_visible_source_stays_visible(update):
    """A source without `source.type` is not given one."""
    source = _bootstrapped_source()
    source["source"] = None

    assert _reconcile(source, PROXY_ENV) is True

    assert "source" not in update.call_args.args[2]
