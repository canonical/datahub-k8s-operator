# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for state validation in charm.py."""

from unittest.mock import patch

import ops
import pytest
from ops import testing

import exceptions


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
        assert "grant" in msg.lower()

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
        assert "grant" in msg.lower()

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
