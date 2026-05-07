# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for services.py."""

import os
from types import SimpleNamespace
from unittest.mock import patch

import literals
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
    context = services.ServiceContext(
        charm=SimpleNamespace(
            _state=state,
            config=config,
            system_client_id=literals.SYSTEM_CLIENT_ID,
            system_client_secret="my-secret",  # nosec B106
        )
    )

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
    context = services.ServiceContext(
        charm=SimpleNamespace(
            _state=state,
            config=config,
            system_client_id=literals.SYSTEM_CLIENT_ID,
            system_client_secret="my-secret",  # nosec B106
        )
    )

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
        system_client_id=literals.SYSTEM_CLIENT_ID,
        system_client_secret="my-secret",  # nosec B106
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.GMSService, "is_enabled", return_value=True):
        env = services.GMSService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == literals.SYSTEM_CLIENT_ID
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"


def test_actions_compile_environment_includes_system_client():
    """Actions environment should include DATAHUB_SYSTEM_CLIENT_ID/SECRET."""
    state = SimpleNamespace(
        kafka_connection={
            "bootstrap_server": "kafka:9092",
            "username": "user",
            "password": "pass",  # nosec
        }
    )
    config = SimpleNamespace(kafka_topic_prefix="")
    charm = SimpleNamespace(
        _state=state,
        config=config,
        system_client_id=literals.SYSTEM_CLIENT_ID,
        system_client_secret="my-secret",  # nosec B106
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.ActionsService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            env = services.ActionsService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == literals.SYSTEM_CLIENT_ID
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"


def test_frontend_compile_environment_includes_system_client():
    """Frontend environment should include DATAHUB_SYSTEM_CLIENT_ID/SECRET."""
    state = SimpleNamespace(
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
    )
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    model = SimpleNamespace(get_secret=lambda id: encryption_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
        use_play_cache_session_store=False,
        oidc_secret_id=None,
        external_fe_hostname="",
    )
    charm = SimpleNamespace(
        _state=state,
        config=config,
        model=model,
        system_client_id=literals.SYSTEM_CLIENT_ID,
        system_client_secret="my-secret",  # nosec B106
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.FrontendService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            env = services.FrontendService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == literals.SYSTEM_CLIENT_ID
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"


class TestFrontendRunInitialization:
    """Tests for FrontendService.run_initialization user.props and truststore steps."""

    @staticmethod
    def _make_context(*, user_props_done=False, password="s3cret"):
        """Build a ServiceContext wired for FrontendService initialization tests."""
        state = SimpleNamespace(
            kafka_connection={
                "bootstrap_server": "k:9092",
                "username": "u",
                "password": "p",
                "initialized": True,
            },  # nosec B105
            opensearch_connection={
                "host": "os",
                "port": "9200",
                "username": "u",
                "password": "p",
                "tls-ca": "PEM-CERT",
                "initialized": True,
            },  # nosec B105
            ran_upgrade=True,
            frontend_user_props_initialized=user_props_done,
        )
        container = SimpleNamespace(push=lambda *a, **kw: None)
        unit = SimpleNamespace(get_container=lambda name: container)
        charm = SimpleNamespace(
            _state=state,
            _ensure_password=lambda: password,
            unit=unit,
        )
        return services.ServiceContext(charm=charm)

    def test_pushes_user_props_when_not_done(self):
        """user.props is pushed and flag is set when not yet initialized."""
        context = self._make_context(user_props_done=False)

        with patch.object(services.FrontendService, "is_ready", return_value=True):
            with patch.object(services, "_import_certificates_to_truststore"):
                result = services.FrontendService.run_initialization(context)

        assert result is True
        assert context.charm._state.frontend_user_props_initialized is True

    def test_truststore_runs_unconditionally(self):
        """Truststore import runs even when user.props is already initialized.

        Truststore self-heals after missing cacerts. _import_certificates_to_truststore
        does its own per-alias dedup, so re-runs are cheap.
        """
        context = self._make_context(user_props_done=True)

        with patch.object(services.FrontendService, "is_ready", return_value=True):
            with patch.object(services, "_import_certificates_to_truststore") as mock_import:
                services.FrontendService.run_initialization(context)

        mock_import.assert_called_once()

    def test_returns_false_when_password_unavailable(self):
        """Returns False without setting flag when password is empty."""
        context = self._make_context(user_props_done=False, password="")  # nosec B106

        with patch.object(services.FrontendService, "is_ready", return_value=True):
            with patch.object(services, "_import_certificates_to_truststore"):
                result = services.FrontendService.run_initialization(context)

        assert result is False
        assert context.charm._state.frontend_user_props_initialized is False


class TestGMSSetWorkloadVersion:
    """Tests for GMSService._set_workload_version."""

    @staticmethod
    def _make_context(*, version_set=False):
        """Build a ServiceContext wired for GMS workload version tests."""
        state = SimpleNamespace(gms_workload_version_set=version_set)
        version_holder = SimpleNamespace(value=None)
        unit = SimpleNamespace(
            get_container=lambda name: SimpleNamespace(pull=lambda path: "version: '1.4.0.5'"),
            set_workload_version=lambda v: setattr(version_holder, "value", v),
        )
        charm = SimpleNamespace(_state=state, unit=unit)
        context = services.ServiceContext(charm=charm)
        return context, version_holder

    def test_sets_version_when_flag_unset(self):
        """Workload version is set and flag is marked True."""
        context, holder = self._make_context(version_set=False)

        services.GMSService._set_workload_version(context)

        assert holder.value == "1.4.0.5"
        assert context.charm._state.gms_workload_version_set is True

    def test_skips_when_flag_already_set(self):
        """Workload version extraction is skipped when already done."""
        context, holder = self._make_context(version_set=True)

        services.GMSService._set_workload_version(context)

        assert holder.value is None

    def test_retries_on_failure(self):
        """Flag stays unset on failure so the operation retries on next call."""
        state = SimpleNamespace(gms_workload_version_set=False)
        unit = SimpleNamespace(
            get_container=lambda name: SimpleNamespace(pull=lambda path: (_ for _ in ()).throw(Exception("not ready"))),
            set_workload_version=lambda v: None,
        )
        charm = SimpleNamespace(_state=state, unit=unit)
        context = services.ServiceContext(charm=charm)

        services.GMSService._set_workload_version(context)

        assert state.gms_workload_version_set is not True
