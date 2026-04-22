# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for state validation in charm.py."""

from unittest.mock import patch

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
