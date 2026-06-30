# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for services.py."""

import os
from types import SimpleNamespace
from unittest.mock import patch

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
    def _container(stdout=None, raises=False):
        """Return a fake GMS container whose psql exec yields ``stdout``."""

        def _exec(*args, **kwargs):
            """Simulate container.exec returning a process with the given stdout."""
            if raises:
                raise RuntimeError("psql unavailable")
            return SimpleNamespace(wait_output=lambda: (stdout, ""))

        return SimpleNamespace(exec=_exec)

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

    def test_false_when_query_fails(self):
        """A failed query is treated as not-provisioned so the bootstrap re-runs."""
        context = SimpleNamespace(charm=_make_charm(db=_db_conn()))
        container = self._container(raises=True)
        assert services.GMSService._backend_is_provisioned(context, container) is False


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
