# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for state validation in charm.py."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ops
import pytest
from ops import testing

import exceptions
import literals
import services as svc
from charm import DatahubK8SOperatorCharm, get_pebble_layer


def _stub_connections(charm):
    """Stubs for the charm's relation connections."""
    charm.db_relation = SimpleNamespace(connection={"host": "db"})
    charm.kafka_relation = SimpleNamespace(connection={"host": "kafka"})
    charm.opensearch_relation = SimpleNamespace(connection={"host": "os"})


class TestGetPasswordAction:
    """Tests for the get-password action handler."""

    def test_returns_password_when_secret_exists(self, charm_ctx, base_state):
        """get-password returns the stored password."""
        with patch.object(DatahubK8SOperatorCharm, "_get_password", return_value="s3cret"):
            charm_ctx.run(charm_ctx.on.action("get-password"), base_state)

        assert charm_ctx.action_results == {"password": "s3cret"}  # nosec B105

    def test_fails_when_password_not_generated(self, charm_ctx, base_state):
        """get-password fails when the password has not been generated yet."""
        with patch.object(DatahubK8SOperatorCharm, "_get_password", return_value=None):
            with pytest.raises(testing.ActionFailed) as exc_info:
                charm_ctx.run(charm_ctx.on.action("get-password"), base_state)

        assert "not been generated" in exc_info.value.message

    def test_fails_on_secret_not_found(self, charm_ctx, base_state):
        """get-password fails when the secret cannot be found."""
        with patch.object(DatahubK8SOperatorCharm, "_get_password", side_effect=ops.SecretNotFoundError("gone")):
            with pytest.raises(testing.ActionFailed) as exc_info:
                charm_ctx.run(charm_ctx.on.action("get-password"), base_state)

        assert "cannot read password secret" in exc_info.value.message

    def test_fails_on_model_error(self, charm_ctx, base_state):
        """get-password fails when the model raises an error reading the secret."""
        with patch.object(DatahubK8SOperatorCharm, "_get_password", side_effect=ops.ModelError("permission denied")):
            with pytest.raises(testing.ActionFailed) as exc_info:
                charm_ctx.run(charm_ctx.on.action("get-password"), base_state)

        assert "cannot read password secret" in exc_info.value.message


_SECRET_ID = "test-encryption-secret-id"  # nosec B105


class TestCheckStateSecretAccess:
    """Tests for secret-access failure handling in _check_state."""

    def test_check_state_raises_unready_on_secret_not_found(self, charm_ctx, base_state):
        """_check_state raises UnreadyStateError when the encryption secret does not exist."""
        with charm_ctx(charm_ctx.on.update_status(), base_state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", side_effect=ops.SecretNotFoundError("not found")):
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        msg = str(exc_info.value)
        assert "encryption-keys-secret-id" in msg
        assert "not found" in msg.lower()

    def test_check_state_raises_unready_on_model_error(self, charm_ctx, base_state):
        """_check_state raises UnreadyStateError when ops raises ModelError reading the secret."""
        with charm_ctx(charm_ctx.on.update_status(), base_state) as mgr:
            with patch.object(
                mgr.charm.model,
                "get_secret",
                side_effect=ops.ModelError("permission denied"),
            ):
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        msg = str(exc_info.value)
        assert "encryption-keys-secret-id" in msg
        assert "permission denied" in msg.lower()

    def test_update_status_blocks_on_secret_not_found(self, charm_ctx, base_state):
        """_on_update_status sets BlockedStatus when the encryption secret cannot be found."""
        with charm_ctx(charm_ctx.on.update_status(), base_state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", side_effect=ops.SecretNotFoundError("not found")):
                state_out = mgr.run()

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "encryption-keys-secret-id" in state_out.unit_status.message

    def test_update_status_blocks_on_model_error(self, charm_ctx, base_state):
        """_on_update_status sets BlockedStatus when ops raises ModelError reading the secret."""
        with charm_ctx(charm_ctx.on.update_status(), base_state) as mgr:
            with patch.object(
                mgr.charm.model,
                "get_secret",
                side_effect=ops.ModelError("permission denied"),
            ):
                state_out = mgr.run()

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "encryption-keys-secret-id" in state_out.unit_status.message


class TestSecretChanged:
    """Tests for the _on_secret_changed handler."""

    def test_secret_changed_calls_reconcile_for_configured_secret(self, charm_ctx, base_state):
        """_on_secret_changed delegates to reconcile when the configured encryption secret changes."""  # noqa W505
        secret = testing.Secret(
            tracked_content={"gms-key": "a", "frontend-key": "b"},
            label="datahub-encryption-keys",
        )
        state = testing.State(config=base_state.config, secrets=[secret])

        with patch.object(DatahubK8SOperatorCharm, "reconcile") as mock_reconcile:
            charm_ctx.run(charm_ctx.on.secret_changed(secret), state)

        mock_reconcile.assert_called_once()

    def test_secret_changed_ignores_unrelated_secret(self, charm_ctx, base_state):
        """_on_secret_changed does nothing for secrets other than the configured encryption key."""
        unrelated_secret = testing.Secret(tracked_content={"some": "data"}, label="something-else")
        state = testing.State(config=base_state.config, secrets=[unrelated_secret])

        with patch.object(DatahubK8SOperatorCharm, "reconcile") as mock_reconcile:
            charm_ctx.run(charm_ctx.on.secret_changed(unrelated_secret), state)

        mock_reconcile.assert_not_called()

    def test_secret_changed_reconciles_when_oauth_related(self, charm_ctx, base_state):
        """_on_secret_changed reconciles for any secret while an oauth relation exists."""
        oauth_rel = testing.Relation(endpoint="oauth", interface="oauth", remote_app_name="hydra")
        oauth_secret = testing.Secret(tracked_content={"secret": "csec"}, label="oauth-client")  # nosec B105
        state = testing.State(config=base_state.config, secrets=[oauth_secret], relations=[oauth_rel])

        with patch.object(DatahubK8SOperatorCharm, "reconcile") as mock_reconcile:
            charm_ctx.run(charm_ctx.on.secret_changed(oauth_secret), state)

        mock_reconcile.assert_called_once()

    def test_secret_changed_blocks_on_inaccessible_secret(self, charm_ctx, base_state):
        """_on_secret_changed sets BlockedStatus when the configured secret is inaccessible."""
        secret = testing.Secret(
            tracked_content={"gms-key": "a", "frontend-key": "b"},
            label="datahub-encryption-keys",
        )
        state = testing.State(config=base_state.config, secrets=[secret])

        with charm_ctx(charm_ctx.on.secret_changed(secret), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", side_effect=ops.SecretNotFoundError("not found")):
                state_out = mgr.run()

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "encryption-keys-secret-id" in state_out.unit_status.message


class TestSystemClientSecret:
    """Tests for system client secret persistence lifecycle."""

    def test_leader_creates_secret_on_first_access(self, charm_ctx):
        """Leader creates an app-owned secret and returns its value on first access."""
        peer = testing.PeerRelation(endpoint="peer")
        state = testing.State(
            config={"encryption-keys-secret-id": _SECRET_ID},
            relations=[peer],
            leader=True,
        )
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            secret = mgr.charm.system_client_secret
            assert secret != ""  # nosec B105
            assert mgr.charm.system_client_secret == secret

    def test_non_leader_returns_empty_before_leader_creates(self, charm_ctx):
        """Non-leader returns empty string when secret has not yet been created."""
        peer = testing.PeerRelation(endpoint="peer")
        state = testing.State(
            config={"encryption-keys-secret-id": _SECRET_ID},
            relations=[peer],
            leader=False,
        )
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            assert mgr.charm.system_client_secret == ""  # nosec B105


class TestCheckStateTrinoPatterns:
    """Tests for trino-patterns config validation in _check_state."""

    @staticmethod
    def _encryption_secret_mock():
        """Return a mock Juju secret with valid encryption key contents."""
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"gms-key": "k1", "frontend-key": "k2"}
        return mock_secret

    @staticmethod
    def _state_with_patterns(base_state, trino_patterns):
        """Build a State with `trino-patterns` set; connections are stubbed at runtime."""
        return testing.State(
            config={**base_state.config, "trino-patterns": trino_patterns},
        )

    def test_check_state_raises_on_invalid_json(self, charm_ctx, base_state):
        """_check_state raises UnreadyStateError when trino-patterns is not valid JSON."""
        state = self._state_with_patterns(base_state, "not-json")
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        assert "trino-patterns" in str(exc_info.value)

    def test_check_state_raises_on_non_dict_json(self, charm_ctx, base_state):
        """_check_state raises UnreadyStateError when trino-patterns is a JSON array."""
        state = self._state_with_patterns(base_state, "[1, 2, 3]")
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        assert "trino-patterns" in str(exc_info.value)
        assert "JSON object" in str(exc_info.value)

    def test_check_state_passes_with_valid_patterns(self, charm_ctx, base_state):
        """_check_state does not raise when trino-patterns is a valid JSON object."""
        valid = json.dumps({"schema-pattern": {"allow": [".*"], "deny": []}})
        state = self._state_with_patterns(base_state, valid)
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                mgr.charm._check_state()

    def test_update_status_blocks_on_invalid_patterns(self, charm_ctx, base_state):
        """_on_update_status sets BlockedStatus when trino-patterns is not valid JSON."""
        state = self._state_with_patterns(base_state, "{{bad}}")
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                state_out = mgr.run()

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "trino-patterns" in state_out.unit_status.message


class TestCheckStateOIDC:
    """Tests for the OIDC-requires-HTTPS guard in _check_state."""

    @staticmethod
    def _encryption_secret_mock():
        """Return a mock Juju secret with valid encryption key contents."""
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"gms-key": "k1", "frontend-key": "k2"}
        return mock_secret

    @staticmethod
    def _state_with_oauth(base_state):
        """Build a State with an oauth relation; connections are stubbed at runtime."""
        oauth_rel = testing.Relation(endpoint="oauth", interface="oauth", remote_app_name="hydra")
        return testing.State(config=base_state.config, relations=[oauth_rel])

    @staticmethod
    def _set_ingress(charm, *, ready, url):
        """Replace frontend_ingress with a stub exposing the given state."""
        charm.frontend_ingress = SimpleNamespace(is_ready=lambda: ready, url=url)

    def test_raises_when_oidc_enabled_and_ingress_not_ready(self, charm_ctx, base_state):
        """_check_state blocks when oauth is related but the ingress hasn't published a URL."""
        state = self._state_with_oauth(base_state)
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                self._set_ingress(mgr.charm, ready=False, url=None)
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        assert "frontend-ingress" in str(exc_info.value)

    def test_raises_when_oidc_url_is_plain_http(self, charm_ctx, base_state):
        """_check_state blocks when oauth is related but the ingress is plain http."""
        state = self._state_with_oauth(base_state)
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                self._set_ingress(mgr.charm, ready=True, url="http://datahub.example/foo")
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        assert "HTTPS" in str(exc_info.value)

    def test_passes_when_oidc_url_is_https(self, charm_ctx, base_state):
        """_check_state allows an HTTPS ingress URL with oauth related."""
        state = self._state_with_oauth(base_state)
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                self._set_ingress(mgr.charm, ready=True, url="https://datahub.example/")
                mgr.charm._check_state()

    def test_passes_when_oidc_url_is_localhost(self, charm_ctx, base_state):
        """_check_state allows http://localhost with oauth related (OAuth 2.0 dev exemption)."""
        state = self._state_with_oauth(base_state)
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                self._set_ingress(mgr.charm, ready=True, url="http://localhost:9002")
                mgr.charm._check_state()

    def test_skipped_when_oidc_disabled(self, charm_ctx, base_state):
        """_check_state does not require ingress when there is no oauth relation."""
        state = testing.State(config=base_state.config)
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                self._set_ingress(mgr.charm, ready=False, url=None)
                mgr.charm._check_state()


class TestOauthClientConfigPublication:
    """Tests for the OAuth client config published from reconcile()."""

    @staticmethod
    def _encryption_secret_mock():
        """Return a mock Juju secret with valid encryption key contents."""
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"gms-key": "k1", "frontend-key": "k2"}
        return mock_secret

    def _run_reconcile_with_oauth(self, charm_ctx, base_state, *, leader):
        """Run config-changed with an oauth relation and an HTTPS ingress, return the state."""
        oauth_rel = testing.Relation(endpoint="oauth", interface="oauth", remote_app_name="hydra")
        state = testing.State(config=base_state.config, relations=[oauth_rel], leader=leader)

        offline_container = MagicMock()
        offline_container.can_connect.return_value = False

        with charm_ctx(charm_ctx.on.config_changed(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                mgr.charm.frontend_ingress = SimpleNamespace(is_ready=lambda: True, url="https://datahub.example")
                with patch.object(mgr.charm.unit, "get_container", return_value=offline_container):
                    state_out = mgr.run()

        return [rel for rel in state_out.relations if rel.endpoint == "oauth"][0]

    def test_leader_publishes_redirect_uri(self, charm_ctx, base_state):
        """The leader publishes the client config with the ingress-derived redirect URI."""
        rel_out = self._run_reconcile_with_oauth(charm_ctx, base_state, leader=True)

        assert rel_out.local_app_data["redirect_uri"] == "https://datahub.example/callback/oidc"
        assert rel_out.local_app_data["scope"] == literals.OAUTH_SCOPE
        assert json.loads(rel_out.local_app_data["grant_types"]) == literals.OAUTH_GRANT_TYPES

    def test_non_leader_does_not_publish(self, charm_ctx, base_state):
        """Non-leader units do not write the requirer databag."""
        rel_out = self._run_reconcile_with_oauth(charm_ctx, base_state, leader=False)

        assert "redirect_uri" not in rel_out.local_app_data


class TestGetPebbleLayer:
    """Tests for the get_pebble_layer helper."""

    def test_services_with_healthcheck_restart_on_a_long_threshold(self):
        """Healthcheck services wire `up` to on-check-failure: restart."""
        context = MagicMock()
        for service in [svc.GMSService, svc.FrontendService]:
            with patch.object(service, "is_enabled", return_value=True):
                with patch.object(service, "compile_environment", return_value=None):
                    layer = get_pebble_layer(service, context)

            on_check_failure = layer["services"][service.name].get("on-check-failure")
            assert (
                on_check_failure.get("up") == "restart"
            ), f"{service.name}: expected on-check-failure up=restart, got {on_check_failure!r}"
            assert (
                layer["checks"]["up"]["threshold"] == literals.HEALTHCHECK_FAILURE_THRESHOLD
            ), f"{service.name}: expected the long restart threshold"


class TestReconcileInitFailure:
    """reconcile() sets BlockedStatus instead of re-raising on initialization failure."""

    @staticmethod
    def _encryption_secret_mock():
        """Return a mock Juju secret with valid encryption key contents."""
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"gms-key": "k1", "frontend-key": "k2"}
        return mock_secret

    def test_init_failure_sets_blocked_status(self, charm_ctx, base_state):
        """run_initialization failure sets BlockedStatus instead of re-raising."""
        fake_container = MagicMock()
        fake_container.can_connect.return_value = True

        # Use config_changed: its handler is a bare self.reconcile() with no
        # extra pebble-health loop before it, so we reach run_initialization directly.
        with charm_ctx(charm_ctx.on.config_changed(), base_state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                _stub_connections(mgr.charm)
                with patch.object(mgr.charm.unit, "get_container", return_value=fake_container):
                    with patch.object(
                        svc.GMSService,
                        "run_initialization",
                        side_effect=exceptions.InitializationFailedError("failed to initialize db"),
                    ):
                        state_out = mgr.run()

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "failed to initialize db" in state_out.unit_status.message
