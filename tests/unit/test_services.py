# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for services.py."""

import datetime
import os
import subprocess  # nosec B404
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ops
import pytest

import exceptions
import literals
import services


def _kafka_conn(initialized=True):  # noqa: D401 — helper
    """Return a Kafka connection dict matching the relation.connection shape."""
    return {
        "bootstrap_server": "kafka:9092",
        "username": "u",
        "password": "p",  # nosec B105
    }


def _db_conn():
    """Return a DB connection dict matching the relation.connection shape."""
    return {
        "host": "db",
        "port": "5432",
        "username": "u",
        "password": "p",  # nosec B105
        "dbname": "datahub_db",
    }


def _os_conn(tls_ca="PEM-CERT"):
    """Return an OpenSearch connection dict matching the relation.connection shape."""
    return {
        "host": "os",
        "port": "9200",
        "username": "u",
        "password": "p",  # nosec B105
        "tls-ca": tls_ca,
    }


def _make_charm(
    *,
    db=None,
    kafka=None,
    opensearch=None,
    config=None,
    model=None,
    frontend_ingress=None,
    oauth_provider=None,
    unit=None,
    ensure_password=None,
):
    """Build a SimpleNamespace charm with the stateless relation surface."""
    return SimpleNamespace(
        db_relation=SimpleNamespace(connection=db),
        kafka_relation=SimpleNamespace(connection=kafka),
        opensearch_relation=SimpleNamespace(connection=opensearch),
        config=config or SimpleNamespace(),
        model=model or SimpleNamespace(),
        frontend_ingress=frontend_ingress or SimpleNamespace(is_ready=lambda: False, url=None),
        oauth_relation=SimpleNamespace(provider_info=oauth_provider),
        unit=unit or SimpleNamespace(),
        system_client_id=literals.SYSTEM_CLIENT_ID,
        system_client_secret="my-secret",  # nosec B106
        _ensure_password=ensure_password or (lambda: "s3cret"),  # nosec B106
    )


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
    config = SimpleNamespace(kafka_topic_prefix="")
    context = services.ServiceContext(charm=_make_charm(kafka=_kafka_conn(), config=config))

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
    config = SimpleNamespace(kafka_topic_prefix="")
    context = services.ServiceContext(charm=_make_charm(kafka=_kafka_conn(), config=config))

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
    """GMS environment should include DATAHUB_SYSTEM_CLIENT_ID/SECRET."""
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    model = SimpleNamespace(get_secret=lambda id: encryption_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
    )
    charm = _make_charm(
        db=_db_conn(),
        kafka=_kafka_conn(),
        opensearch=_os_conn(tls_ca="cert"),
        config=config,
        model=model,
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.GMSService, "is_enabled", return_value=True):
        env = services.GMSService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == literals.SYSTEM_CLIENT_ID
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"


def test_gms_compile_environment_enables_prometheus():
    """GMS environment should enable the JMX-Prometheus exporter for COS scraping."""
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    model = SimpleNamespace(get_secret=lambda id: encryption_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
    )
    charm = _make_charm(
        db=_db_conn(),
        kafka=_kafka_conn(),
        opensearch=_os_conn(tls_ca="cert"),
        config=config,
        model=model,
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.GMSService, "is_enabled", return_value=True):
        env = services.GMSService.compile_environment(context)

    assert env is not None
    assert env["ENABLE_PROMETHEUS"] == "true"


def test_gms_compile_environment_polls_for_the_upgrade_record_at_a_fixed_interval():
    """GMS checks for the upgrade job's record every 10s instead of on a growing backoff."""
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
    )
    charm = _make_charm(
        db=_db_conn(),
        kafka=_kafka_conn(),
        opensearch=_os_conn(tls_ca="cert"),
        config=config,
        model=SimpleNamespace(get_secret=lambda id: encryption_secret),
    )

    with patch.object(services.GMSService, "is_enabled", return_value=True):
        env = services.GMSService.compile_environment(services.ServiceContext(charm=charm))

    assert env["BOOTSTRAP_SYSTEM_UPDATE_INITIAL_BACK_OFF_MILLIS"] == "10000"
    assert env["BOOTSTRAP_SYSTEM_UPDATE_BACK_OFF_FACTOR"] == "1"
    assert env["BOOTSTRAP_SYSTEM_UPDATE_MAX_BACK_OFFS"] == "360"


def test_actions_compile_environment_includes_system_client():
    """Actions environment should include DATAHUB_SYSTEM_CLIENT_ID/SECRET."""
    config = SimpleNamespace(kafka_topic_prefix="")
    context = services.ServiceContext(charm=_make_charm(kafka=_kafka_conn(), config=config))

    with patch.object(services.ActionsService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            env = services.ActionsService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == literals.SYSTEM_CLIENT_ID
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"


def test_frontend_compile_environment_includes_system_client():
    """Frontend environment should include DATAHUB_SYSTEM_CLIENT_ID/SECRET."""
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    model = SimpleNamespace(get_secret=lambda id: encryption_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
        use_play_cache_session_store=False,
    )
    charm = _make_charm(
        kafka=_kafka_conn(),
        opensearch=_os_conn(tls_ca="cert"),
        config=config,
        model=model,
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.FrontendService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            env = services.FrontendService.compile_environment(context)

    assert env is not None
    assert env["DATAHUB_SYSTEM_CLIENT_ID"] == literals.SYSTEM_CLIENT_ID
    assert env["DATAHUB_SYSTEM_CLIENT_SECRET"] == "my-secret"


def test_frontend_compile_environment_enables_prometheus():
    """Frontend environment should enable the JMX-Prometheus exporter for COS scraping."""
    encryption_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "secret123", "frontend-key": "secret456"},
    )
    model = SimpleNamespace(get_secret=lambda id: encryption_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
        use_play_cache_session_store=False,
    )
    charm = _make_charm(
        kafka=_kafka_conn(),
        opensearch=_os_conn(tls_ca="cert"),
        config=config,
        model=model,
    )
    context = services.ServiceContext(charm=charm)

    with patch.object(services.FrontendService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            env = services.FrontendService.compile_environment(context)

    assert env is not None
    assert env["ENABLE_PROMETHEUS"] == "true"


def _oauth_provider(issuer_url="https://idp.example"):
    """Return a stub of the oauth provider info delivered over the relation."""
    return SimpleNamespace(issuer_url=issuer_url, client_id="cid", client_secret="csec")  # nosec B106


def _frontend_oidc_env(*, ingress_ready, ingress_url, oauth_provider="default"):
    """Build env from FrontendService with a stub ingress and oauth provider info."""
    enc_secret = SimpleNamespace(
        get_content=lambda refresh=False: {"gms-key": "k1", "frontend-key": "k2"},
    )
    model = SimpleNamespace(get_secret=lambda id: enc_secret)
    config = SimpleNamespace(
        encryption_keys_secret_id="enc-id",  # nosec B106
        opensearch_index_prefix="",
        kafka_topic_prefix="",
        use_play_cache_session_store=False,
    )
    charm = _make_charm(
        kafka=_kafka_conn(),
        opensearch=_os_conn(tls_ca="cert"),
        config=config,
        model=model,
        frontend_ingress=SimpleNamespace(is_ready=lambda: ingress_ready, url=ingress_url),
        oauth_provider=_oauth_provider() if oauth_provider == "default" else oauth_provider,
    )
    context = services.ServiceContext(charm=charm)
    with patch.object(services.FrontendService, "is_enabled", return_value=True):
        with patch.dict(os.environ, {}, clear=True):
            return services.FrontendService.compile_environment(context)


def test_frontend_oidc_base_url_uses_ingress_when_ready():
    """OIDC base URL is set from the ingress URL when the ingress relation is ready."""
    env = _frontend_oidc_env(ingress_ready=True, ingress_url="https://traefik.example/datahub")
    assert env["AUTH_OIDC_BASE_URL"] == "https://traefik.example/datahub"


def test_frontend_oidc_base_url_falls_back_when_ingress_not_ready():
    """OIDC base URL falls back to FRONTEND_FALLBACK_URL when the ingress relation is not ready."""
    env = _frontend_oidc_env(ingress_ready=False, ingress_url=None)
    assert env["AUTH_OIDC_BASE_URL"] == literals.FRONTEND_FALLBACK_URL


def test_frontend_oidc_base_url_strips_trailing_slash():
    """A root-serving ingress URL ('https://host/') is normalized (no trailing slash)."""
    env = _frontend_oidc_env(ingress_ready=True, ingress_url="https://datahub.example/")
    assert env["AUTH_OIDC_BASE_URL"] == "https://datahub.example"


def test_frontend_oidc_env_from_provider_info():
    """OIDC env derives discovery URI and credentials from the oauth provider info."""
    env = _frontend_oidc_env(ingress_ready=True, ingress_url="https://traefik.example/datahub")
    assert env["AUTH_OIDC_ENABLED"] == "true"
    assert env["AUTH_OIDC_DISCOVERY_URI"] == "https://idp.example/.well-known/openid-configuration"
    assert env["AUTH_OIDC_SCOPE"] == literals.OAUTH_SCOPE
    assert env["AUTH_OIDC_CLIENT_ID"] == "cid"
    assert env["AUTH_OIDC_CLIENT_SECRET"] == "csec"
    assert env["AUTH_OIDC_USER_NAME_CLAIM"] == "email"


def test_frontend_oidc_discovery_uri_handles_trailing_slash():
    """Discovery URI does not double the slash when the issuer URL ends with one."""
    env = _frontend_oidc_env(
        ingress_ready=True,
        ingress_url="https://traefik.example/datahub",
        oauth_provider=_oauth_provider(issuer_url="https://idp.example/"),
    )
    assert env["AUTH_OIDC_DISCOVERY_URI"] == "https://idp.example/.well-known/openid-configuration"


def test_frontend_oidc_env_absent_without_provider_info():
    """No AUTH_OIDC_* variables are rendered when no provider info is available."""
    env = _frontend_oidc_env(ingress_ready=True, ingress_url="https://traefik.example/datahub", oauth_provider=None)
    assert not [key for key in env if key.startswith("AUTH_OIDC_")]


class TestFrontendRunInitialization:
    """Tests for FrontendService.run_initialization user.props and truststore steps."""

    @staticmethod
    def _make_context(*, password="s3cret"):
        """Build a ServiceContext wired for FrontendService initialization tests."""
        pushed: list = []
        container = SimpleNamespace(push=lambda *args, **kwargs: pushed.append((args, kwargs)))
        unit = SimpleNamespace(get_container=lambda name: container)
        charm = _make_charm(
            kafka=_kafka_conn(),
            opensearch=_os_conn(),
            unit=unit,
            ensure_password=lambda: password,
        )
        return services.ServiceContext(charm=charm), pushed

    def test_truststore_runs_unconditionally(self):
        """Truststore import runs on every reconcile, regardless of prior runs."""
        context, _pushed = self._make_context()

        with patch.object(services.FrontendService, "is_ready", return_value=True):
            with patch.object(services, "_import_certificates_to_truststore") as mock_import:
                container = context.charm.unit.get_container("datahub-frontend")
                container.pull = lambda path: SimpleNamespace(read=lambda: "x")
                services.FrontendService.run_initialization(context)

        mock_import.assert_called_once()

    def test_returns_false_when_password_unavailable(self):
        """Returns False when the admin password has not been generated yet."""
        context, _pushed = self._make_context(password="")  # nosec B106

        with patch.object(services.FrontendService, "is_ready", return_value=True):
            with patch.object(services, "_import_certificates_to_truststore"):
                result = services.FrontendService.run_initialization(context)

        assert result is False


class TestBackendProvisionedGate:
    """Tests for GMSService._backend_is_provisioned (the one-time-bootstrap gate)."""

    @staticmethod
    def _container(stdout=None, raises=None):
        """Return a fake GMS container whose psql exec yields ``stdout`` or raises ``raises``."""

        def _wait_output():
            """Simulate process.wait_output, raising like psql's non-zero exits do."""
            if raises is not None:
                raise raises
            return stdout, ""

        return SimpleNamespace(exec=lambda *args, **kwargs: SimpleNamespace(wait_output=_wait_output))

    def test_true_when_policy_aspect_present(self):
        """Provisioned when the query finds the root policy aspect."""
        context = SimpleNamespace(charm=_make_charm(db=_db_conn()))
        container = self._container(stdout="1\n")
        assert services.GMSService._backend_is_provisioned(context, container) is True

    def test_false_when_policy_aspect_absent(self):
        """Not provisioned when the query returns no rows (fresh backend)."""
        context = SimpleNamespace(charm=_make_charm(db=_db_conn()))
        container = self._container(stdout="")
        assert services.GMSService._backend_is_provisioned(context, container) is False

    def test_false_when_no_db_connection(self):
        """Not provisioned when the DB relation has no connection yet."""
        context = SimpleNamespace(charm=_make_charm(db=None))
        container = self._container(stdout="1\n")
        assert services.GMSService._backend_is_provisioned(context, container) is False

    def test_false_when_the_table_is_missing(self):
        """An SQL error (psql exit 1), as on a database never bootstrapped, is not provisioned."""
        context = SimpleNamespace(charm=_make_charm(db=_db_conn()))
        error = ops.pebble.ExecError(["psql"], 1, "", 'ERROR:  relation "metadata_aspect_v2" does not exist')
        container = self._container(raises=error)
        assert services.GMSService._backend_is_provisioned(context, container) is False

    def test_unreachable_when_psql_cannot_connect(self):
        """A connection failure (psql exit 2) is an unreachable backend, not an empty one."""
        context = SimpleNamespace(charm=_make_charm(db=_db_conn()))
        error = ops.pebble.ExecError(["psql"], 2, "", "psql: error: connection refused\n")
        container = self._container(raises=error)
        with pytest.raises(exceptions.BackendUnreachableError, match="cannot query PostgreSQL: psql: error"):
            services.GMSService._backend_is_provisioned(context, container)

    def test_false_when_psql_cannot_run(self):
        """Any other failure to run the query is treated as not provisioned."""
        context = SimpleNamespace(charm=_make_charm(db=_db_conn()))
        container = self._container(raises=RuntimeError("psql unavailable"))
        assert services.GMSService._backend_is_provisioned(context, container) is False


class TestWorkloadIsRunningGate:
    """Tests for GMSService._workload_is_running (the in-flight-bootstrap gate)."""

    @staticmethod
    def _container(services_map):
        """Return a fake container whose ``get_services`` yields ``services_map``."""
        return SimpleNamespace(get_services=lambda name: services_map)

    def test_true_when_service_running(self):
        """Running when pebble reports the service as up."""
        container = self._container({"datahub-gms": SimpleNamespace(is_running=lambda: True)})
        assert services.GMSService._workload_is_running(container) is True

    def test_false_when_service_stopped(self):
        """Not running when pebble reports the service as down."""
        container = self._container({"datahub-gms": SimpleNamespace(is_running=lambda: False)})
        assert services.GMSService._workload_is_running(container) is False

    def test_false_when_layer_not_added_yet(self):
        """Not running on a fresh container whose layer has not been added.

        This is the first-reconcile case, where the upgrade job must still run.
        """
        assert services.GMSService._workload_is_running(self._container({})) is False


class TestGMSSetWorkloadVersion:
    """Tests for GMSService._set_workload_version (stateless — always tries to set)."""

    def test_sets_version(self):
        """Workload version is set from the container's rockcraft.yaml."""
        version_holder = SimpleNamespace(value=None)
        unit = SimpleNamespace(
            get_container=lambda name: SimpleNamespace(pull=lambda path: "version: '1.4.0.5'"),
            set_workload_version=lambda v: setattr(version_holder, "value", v),
        )
        charm = _make_charm(unit=unit)
        context = services.ServiceContext(charm=charm)

        services.GMSService._set_workload_version(context)

        assert version_holder.value == "1.4.0.5"


class TestGMSRunInitialization:
    """Tests for when GMSService.run_initialization starts, holds and reports the upgrade job.

    Attributes:
        ERRORS: The exceptions run_initialization reports the upgrade job with.
    """

    ERRORS = (exceptions.BackendDriftError, exceptions.BackendRestoringError, exceptions.BackendRetryingError)

    @classmethod
    def _run(
        cls, *, leader=True, provisioned=True, drift=(), job=None, last_exit=None, env_changed=False, running=False
    ):
        """Run GMS initialization with the backend probes and the pebble job replaced by mocks.

        Args:
            leader: Whether the unit is the leader.
            provisioned: Result of the Postgres marker query.
            drift: What `_backend_drift` returns.
            job: Pebble status of the upgrade service, None if it is not planned.
            last_exit: Exit code of the job's last run in this container, None if none.
            env_changed: Whether the planned job environment is out of date.
            running: Whether pebble reports GMS as running.

        Returns:
            The mocks keyed by name, and the raised exception or None.
        """
        container = MagicMock()
        unit = SimpleNamespace(get_container=lambda name: container, is_leader=lambda: leader)
        context = services.ServiceContext(charm=_make_charm(unit=unit))
        gms = services.GMSService
        mocks = {}
        with ExitStack() as stack:
            for name, kwargs in {
                "is_ready": {"return_value": True},
                "_set_workload_version": {},
                "_backend_is_provisioned": {"return_value": provisioned},
                "_backend_drift": {"return_value": list(drift)},
                "_upgrade_status": {"return_value": job},
                "_upgrade_last_exit": {"return_value": last_exit},
                "_upgrade_environment_changed": {"return_value": env_changed},
                "_workload_is_running": {"return_value": running},
                "_start_upgrade": {},
                "_stop_upgrade": {},
                "_run_postgresql_setup": {},
                "_run_opensearch_setup": {},
                "_run_truststore_init": {},
            }.items():
                mocks[name] = stack.enter_context(patch.object(gms, name, **kwargs))
            try:
                gms.run_initialization(context)
            except cls.ERRORS as e:
                return mocks, e
        return mocks, None

    def test_provisioned_backend_without_drift_skips_the_job(self):
        """A rebuilt pod against intact backends does not rerun SystemUpdate."""
        mocks, error = self._run()
        mocks["_start_upgrade"].assert_not_called()
        assert error is None

    def test_no_drift_stops_the_job(self):
        """Pebble's reruns end once the backends are complete."""
        mocks, error = self._run(job="backoff", last_exit=0)
        mocks["_stop_upgrade"].assert_called_once()
        assert error is None

    def test_fresh_backend_runs_the_bootstrap(self):
        """First deploy: setup scripts run and the job is started."""
        mocks, error = self._run(provisioned=False, drift=["missing Kafka topics: X"])
        mocks["_run_postgresql_setup"].assert_called_once()
        mocks["_start_upgrade"].assert_called_once()
        assert isinstance(error, exceptions.BackendRestoringError)
        assert str(error) == "upgrade job running: missing Kafka topics: X"

    def test_running_job_is_reported_and_not_started_again(self):
        """While the job runs nothing else is decided."""
        mocks, error = self._run(drift=["missing Kafka topics: X"], job="active")
        mocks["_start_upgrade"].assert_not_called()
        mocks["_backend_drift"].assert_not_called()
        assert isinstance(error, exceptions.BackendRestoringError)

    def test_unprovisioned_backend_runs_the_job(self):
        """A wiped Postgres behind intact Kafka and OpenSearch reruns the bootstrap steps."""
        mocks, error = self._run(provisioned=False)
        mocks["_start_upgrade"].assert_called_once()
        assert str(error) == "upgrade job running: backend not provisioned"

    def test_bootstrap_after_a_successful_run_is_in_flight(self):
        """Marker not written yet after a successful run here, before GMS has started."""
        mocks, error = self._run(provisioned=False, job="backoff", last_exit=0)
        mocks["_start_upgrade"].assert_not_called()
        mocks["_stop_upgrade"].assert_called_once()
        assert error is None

    def test_failed_bootstrap_is_retried_not_stopped(self):
        """A first run that failed (Postgres down) stays up for pebble to retry."""
        mocks, error = self._run(provisioned=False, job="backoff", last_exit=1)
        mocks["_stop_upgrade"].assert_not_called()
        mocks["_start_upgrade"].assert_not_called()
        assert isinstance(error, exceptions.BackendRetryingError)
        assert str(error) == "upgrade job failed (exit 1), pebble retries it: backend not provisioned"

    def test_failed_bootstrap_that_was_stopped_starts_again(self):
        """A bootstrap run stopped before it finished (SIGTERM, 143) is not taken for success."""
        mocks, _ = self._run(provisioned=False, job="inactive", last_exit=143)
        mocks["_start_upgrade"].assert_called_once()

    def test_in_flight_bootstrap_with_gms_running_is_left_alone(self):
        """Marker not written yet while GMS is running and Kafka/OpenSearch are complete."""
        mocks, error = self._run(provisioned=False, running=True)
        mocks["_start_upgrade"].assert_not_called()
        assert error is None

    def test_drift_starts_the_job_even_while_gms_runs(self):
        """A lost topic or upgrade record is repaired on a provisioned, running deployment."""
        mocks, error = self._run(drift=["no v1.4.0.5 record"], running=True)
        mocks["_start_upgrade"].assert_called_once()
        mocks["_run_postgresql_setup"].assert_not_called()
        assert isinstance(error, exceptions.BackendRestoringError)

    def test_drift_starts_a_job_that_was_stopped(self):
        """New drift after an earlier repair starts the job again at once."""
        mocks, error = self._run(drift=["missing Kafka topics: Y"], job="inactive", last_exit=0)
        mocks["_start_upgrade"].assert_called_once()
        assert isinstance(error, exceptions.BackendRestoringError)

    def test_drift_left_by_a_successful_run_blocks(self):
        """Drift that outlived a successful run needs an operator; pebble paces the reruns."""
        mocks, error = self._run(drift=["missing Kafka topics: X"], job="backoff", last_exit=0)
        mocks["_start_upgrade"].assert_not_called()
        mocks["_stop_upgrade"].assert_not_called()
        assert isinstance(error, exceptions.BackendDriftError)
        assert str(error) == "upgrade job ran but did not restore: missing Kafka topics: X"

    def test_failed_run_waits_for_pebble(self):
        """A failed run is retried by pebble without the charm starting anything."""
        mocks, error = self._run(drift=["missing Kafka topics: X"], job="backoff", last_exit=137)
        mocks["_start_upgrade"].assert_not_called()
        assert isinstance(error, exceptions.BackendRetryingError)
        assert str(error) == "upgrade job failed (exit 137), pebble retries it: missing Kafka topics: X"

    def test_changed_relation_data_restarts_a_job_in_backoff(self):
        """Pebble reruns would keep the old credentials, so the job is replanned and restarted."""
        mocks, error = self._run(drift=["missing Kafka topics: X"], job="backoff", last_exit=1, env_changed=True)
        mocks["_start_upgrade"].assert_called_once()
        assert isinstance(error, exceptions.BackendRestoringError)

    def test_follower_reports_drift_without_running_the_job(self):
        """Only the leader repairs; a follower waits for it and stops a job it was running."""
        mocks, error = self._run(leader=False, drift=["missing Kafka topics: X"])
        mocks["_start_upgrade"].assert_not_called()
        mocks["_stop_upgrade"].assert_called_once()
        assert str(error) == "waiting for the leader to restore: missing Kafka topics: X"


def _notice(rc):
    """Build the pebble notice the upgrade script records on exit.

    Args:
        rc: Exit code carried in the notice data, or None for no data.

    Returns:
        A pebble Notice.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    return ops.pebble.Notice(
        id="1",
        user_id=0,
        type=ops.pebble.NoticeType.CUSTOM,
        key=literals.UPGRADE_NOTICE_KEY,
        first_occurred=now,
        last_occurred=now,
        last_repeated=now,
        occurrences=1,
        last_data={} if rc is None else {"rc": rc},
    )


def _service(current):
    """Build pebble's view of the upgrade service.

    Args:
        current: The status pebble reports.

    Returns:
        A pebble ServiceInfo keyed by the upgrade service name.
    """
    info = ops.pebble.ServiceInfo.from_dict(
        {"name": literals.UPGRADE_SERVICE_NAME, "startup": "disabled", "current": current}
    )
    return {literals.UPGRADE_SERVICE_NAME: info}


class TestGMSUpgradeJob:
    """Tests for the pebble service that runs SystemUpdate."""

    @pytest.mark.parametrize("current", ["active", "backoff", "inactive", "error"])
    def test_upgrade_status_reads_every_pebble_status(self, current):
        """`backoff`, which ops does not model, is read like the modelled statuses."""
        container = MagicMock()
        container.get_services.return_value = _service(current)
        assert services.GMSService._upgrade_status(container) == current

    def test_upgrade_status_without_the_service(self):
        """A container whose upgrade layer was never added has no status."""
        container = MagicMock()
        container.get_services.return_value = {}
        assert services.GMSService._upgrade_status(container) is None

    def test_start_plans_and_starts_the_service(self):
        """The script is pushed, the layer carries the job environment, and the service starts."""
        container = MagicMock()
        context = services.ServiceContext(
            charm=_make_charm(
                db=_db_conn(),
                kafka=_kafka_conn(),
                opensearch=_os_conn(),
                config=SimpleNamespace(kafka_topic_prefix="", opensearch_index_prefix=""),
            )
        )
        with patch.object(services.utils, "push_contents_to_file") as push:
            services.GMSService._start_upgrade(context, container)

        push.assert_called_once_with(container, literals.UPGRADE_SCRIPT, literals.UPGRADE_SCRIPT_PATH, 0o755)
        label, layer = container.add_layer.call_args.args[:2]
        service = layer["services"][literals.UPGRADE_SERVICE_NAME]
        assert label == literals.UPGRADE_SERVICE_NAME
        assert service["startup"] == "disabled"
        assert service["on-success"] == "restart"
        assert service["on-failure"] == "restart"
        assert service["backoff-limit"] == literals.UPGRADE_BACKOFF_LIMIT
        assert service["environment"]["KAFKA_BOOTSTRAP_SERVER"] == _kafka_conn()["bootstrap_server"]
        container.restart.assert_called_once_with(literals.UPGRADE_SERVICE_NAME)

    def test_start_failure_is_drift(self):
        """A job pebble cannot start is reported rather than crashing the hook."""
        container = MagicMock()
        container.restart.side_effect = ops.pebble.ChangeError("exited quickly", MagicMock())
        context = services.ServiceContext(
            charm=_make_charm(
                db=_db_conn(),
                kafka=_kafka_conn(),
                opensearch=_os_conn(),
                config=SimpleNamespace(kafka_topic_prefix="", opensearch_index_prefix=""),
            )
        )
        with patch.object(services.utils, "push_contents_to_file"):
            with pytest.raises(exceptions.BackendDriftError, match="upgrade job did not start"):
                services.GMSService._start_upgrade(context, container)

    @pytest.mark.parametrize("current, stopped", [("active", True), ("backoff", True), ("inactive", False)])
    def test_stop_only_touches_a_live_job(self, current, stopped):
        """Stopping is a no-op for a job that already exited."""
        container = MagicMock()
        container.get_services.return_value = _service(current)
        services.GMSService._stop_upgrade(container)
        assert container.stop.called is stopped

    @pytest.mark.parametrize(
        "job, gms_running, expected",
        [("active", False, True), ("active", True, False), ("inactive", False, False), (None, False, False)],
    )
    def test_gms_waits_only_for_a_running_job(self, job, gms_running, expected):
        """GMS is held back only when it is not running yet and the job is."""
        gms = services.GMSService
        with patch.object(gms, "_upgrade_status", return_value=job):
            with patch.object(gms, "_workload_is_running", return_value=gms_running):
                assert gms.is_waiting_for_upgrade(MagicMock()) is expected

    @pytest.mark.parametrize("notices, expected", [([], None), ([_notice("0")], 0), ([_notice("137")], 137)])
    def test_last_exit_reads_the_exit_notice(self, notices, expected):
        """The exit code comes from the data of the job's exit notice."""
        container = MagicMock()
        container.get_notices.return_value = notices
        assert services.GMSService._upgrade_last_exit(container) == expected
        container.get_notices.assert_called_once_with(
            types=[ops.pebble.NoticeType.CUSTOM], keys=[literals.UPGRADE_NOTICE_KEY]
        )

    def test_last_exit_without_a_code(self):
        """A notice without a usable exit code counts as no recorded run."""
        container = MagicMock()
        container.get_notices.return_value = [_notice(None)]
        assert services.GMSService._upgrade_last_exit(container) is None

    @pytest.mark.parametrize(
        "planned, expected",
        [(None, True), ({"KAFKA_BOOTSTRAP_SERVER": "old:9092"}, True), ("current", False)],
    )
    def test_environment_changed_compares_the_plan(self, planned, expected):
        """The planned job environment is compared with the one the relations give now."""
        context = services.ServiceContext(
            charm=_make_charm(
                db=_db_conn(),
                kafka=_kafka_conn(),
                opensearch=_os_conn(),
                config=SimpleNamespace(kafka_topic_prefix="", opensearch_index_prefix=""),
            )
        )
        current = services.GMSService._compile_upgrade_environment(context)
        container = MagicMock()
        plan_services = {}
        if planned is not None:
            env = current if planned == "current" else planned
            plan_services[literals.UPGRADE_SERVICE_NAME] = ops.pebble.Service(
                literals.UPGRADE_SERVICE_NAME, {"environment": dict(env)}
            )
        container.get_plan.return_value = SimpleNamespace(services=plan_services)
        assert services.GMSService._upgrade_environment_changed(context, container) is expected


class TestUpgradeScript:
    """Run the upgrade script with stub `java` and `pebble` binaries."""

    @staticmethod
    def _run(tmp_path, java_exit):
        """Run the rendered script with a java stub exiting ``java_exit``.

        Returns:
            The script's exit code and the arguments pebble was called with.
        """
        java = tmp_path / "java"
        java.write_text(f'#!/bin/bash\necho "$@" > {tmp_path}/java.args\nexit {java_exit}\n')
        pebble = tmp_path / "pebble"
        pebble.write_text(f'#!/bin/bash\necho "$@" >> {tmp_path}/pebble.args\n')
        for stub in (java, pebble):
            stub.chmod(0o755)
        script = tmp_path / "run-upgrade.sh"
        script.write_text(
            literals.UPGRADE_SCRIPT.replace(literals.JAVA_BIN_PATH, str(java)).replace(
                literals.PEBBLE_BIN_PATH, str(pebble)
            )
        )
        result = subprocess.run(["bash", str(script)], check=False)  # nosec B603 B607
        return result.returncode, (tmp_path / "pebble.args").read_text().strip()

    @pytest.mark.parametrize("java_exit", [0, 3])
    def test_exit_code_reaches_pebble_and_the_notice(self, tmp_path, java_exit):
        """The job's exit code is the service's exit code and rides on the exit notice."""
        rc, pebble_args = self._run(tmp_path, java_exit)
        assert rc == java_exit
        assert pebble_args == f"notify {literals.UPGRADE_NOTICE_KEY} rc={java_exit}"

    def test_runs_system_update(self, tmp_path):
        """The jar is run with the SystemUpdate upgrade id."""
        self._run(tmp_path, 0)
        assert (tmp_path / "java.args").read_text().strip() == f"-jar {literals.UPGRADE_JAR_PATH} -u SystemUpdate"

    def test_timeout_stays_in_the_service_process_group(self):
        """Pebble stops a service by signalling its process group, which timeout must stay in."""
        assert f"timeout --foreground --kill-after={literals.UPGRADE_KILL_AFTER} {literals.UPGRADE_TIMEOUT}" in (
            literals.UPGRADE_SCRIPT
        )


class TestGMSBackendDrift:
    """Tests for GMSService._backend_drift wiring the charm config into the probes."""

    def test_probes_use_prefixed_names_and_the_rock_version(self):
        """Topic names, the history topic, the version and the index prefix reach the probes."""
        charm = _make_charm(
            kafka=_kafka_conn(),
            opensearch=_os_conn(),
            config=SimpleNamespace(kafka_topic_prefix="dh", opensearch_index_prefix="ix"),
        )
        files = {
            "/rockcraft.yaml": "version: '1.4.0.5'",
            literals.ENTITY_REGISTRY_PATH: "entities:\n  - name: dataset\n  - name: tag\n",
        }
        container = SimpleNamespace(pull=lambda path: files[path])
        with patch.object(services.backend_probes, "kafka_drift", return_value=["k"]) as kafka_drift:
            with patch.object(services.backend_probes, "opensearch_drift", return_value=["o"]) as os_drift:
                drift = services.GMSService._backend_drift(services.ServiceContext(charm=charm), container)

        assert drift == ["k", "o"]
        _conn, topics, history, version = kafka_drift.call_args.args
        assert "dh_MetadataChangeProposal_v1" in topics
        assert history == "dh_DataHubUpgradeHistory_v1"
        assert version == "1.4.0.5"
        os_drift.assert_called_once_with(_os_conn(), "ix", ["dataset", "tag"])

    def test_unreadable_registry_skips_the_mapping_check(self):
        """A rock without a readable registry gives the probe no entity names."""

        def _pull(path):
            """Fail like pull on a missing file."""
            raise ops.pebble.PathError("not-found", path)

        assert services.GMSService._entity_names(SimpleNamespace(pull=_pull)) is None
