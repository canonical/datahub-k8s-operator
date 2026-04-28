# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for state validation in charm.py."""

import json
from unittest.mock import MagicMock, patch

import ops
import pytest
from ops import testing

import exceptions
from charm import DatahubK8SOperatorCharm


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

    def test_secret_changed_calls_update_for_configured_secret(self, charm_ctx, base_state):
        """_on_secret_changed delegates to _update when the configured encryption secret changes."""  # noqa W505
        secret = testing.Secret(
            tracked_content={"gms-key": "a", "frontend-key": "b"},
            label="datahub-encryption-keys",
        )
        state = testing.State(config=base_state.config, secrets=[secret])

        with patch.object(DatahubK8SOperatorCharm, "_update") as mock_update:
            charm_ctx.run(charm_ctx.on.secret_changed(secret), state)

        mock_update.assert_called_once()

    def test_secret_changed_ignores_unrelated_secret(self, charm_ctx, base_state):
        """_on_secret_changed does nothing for secrets other than the configured encryption key."""
        unrelated_secret = testing.Secret(tracked_content={"some": "data"}, label="something-else")
        state = testing.State(config=base_state.config, secrets=[unrelated_secret])

        with patch.object(DatahubK8SOperatorCharm, "_update") as mock_update:
            charm_ctx.run(charm_ctx.on.secret_changed(unrelated_secret), state)

        mock_update.assert_not_called()

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

    def test_returns_empty_without_peer_relation(self, charm_ctx):
        """Returns empty string when peer relation is not yet available."""
        state = testing.State(
            config={"encryption-keys-secret-id": _SECRET_ID},
            leader=True,
        )
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            assert mgr.charm.system_client_secret == ""  # nosec B105

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
        """Build a test State that passes all checks before trino-patterns validation."""
        peer = testing.PeerRelation(
            endpoint="peer",
            local_app_data={
                "database_connection": json.dumps({"host": "db"}),
                "kafka_connection": json.dumps({"host": "kafka"}),
                "opensearch_connection": json.dumps({"host": "os"}),
            },
        )
        return testing.State(
            config={**base_state.config, "trino-patterns": trino_patterns},
            relations=[peer],
        )

    def test_check_state_raises_on_invalid_json(self, charm_ctx, base_state):
        """_check_state raises UnreadyStateError when trino-patterns is not valid JSON."""
        state = self._state_with_patterns(base_state, "not-json")
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                    mgr.charm._check_state()

        assert "trino-patterns" in str(exc_info.value)

    def test_check_state_raises_on_non_dict_json(self, charm_ctx, base_state):
        """_check_state raises UnreadyStateError when trino-patterns is a JSON array."""
        state = self._state_with_patterns(base_state, "[1, 2, 3]")
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
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
                mgr.charm._check_state()

    def test_update_status_blocks_on_invalid_patterns(self, charm_ctx, base_state):
        """_on_update_status sets BlockedStatus when trino-patterns is not valid JSON."""
        state = self._state_with_patterns(base_state, "{{bad}}")
        with charm_ctx(charm_ctx.on.update_status(), state) as mgr:
            with patch.object(mgr.charm.model, "get_secret", return_value=self._encryption_secret_mock()):
                state_out = mgr.run()

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "trino-patterns" in state_out.unit_status.message
