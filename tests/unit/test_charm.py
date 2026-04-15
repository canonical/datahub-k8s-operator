# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for state validation in charm.py."""

from unittest.mock import patch

import ops
import pytest

import exceptions


class TestCheckStateSecretAccess:
    """Tests for secret-access failure handling in _check_state."""

    def test_check_state_raises_unready_on_secret_not_found(self, charm_harness):
        """_check_state raises UnreadyStateError when the encryption secret does not exist."""
        with patch.object(charm_harness.charm.model, "get_secret", side_effect=ops.SecretNotFoundError("not found")):
            with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                charm_harness.charm._check_state()

        msg = str(exc_info.value)
        assert "encryption-keys-secret-id" in msg
        assert "grant" in msg.lower()

    def test_check_state_raises_unready_on_model_error(self, charm_harness):
        """_check_state raises UnreadyStateError when ops raises ModelError reading the secret."""
        with patch.object(
            charm_harness.charm.model,
            "get_secret",
            side_effect=ops.ModelError("permission denied"),
        ):
            with pytest.raises(exceptions.UnreadyStateError) as exc_info:
                charm_harness.charm._check_state()

        msg = str(exc_info.value)
        assert "encryption-keys-secret-id" in msg
        assert "grant" in msg.lower()

    def test_update_status_blocks_on_secret_not_found(self, charm_harness):
        """_on_update_status sets BlockedStatus when the encryption secret cannot be found."""
        with patch.object(charm_harness.charm.model, "get_secret", side_effect=ops.SecretNotFoundError("not found")):
            charm_harness.charm.on.update_status.emit()

        assert isinstance(charm_harness.charm.unit.status, ops.BlockedStatus)
        assert "encryption-keys-secret-id" in charm_harness.charm.unit.status.message

    def test_update_status_blocks_on_model_error(self, charm_harness):
        """_on_update_status sets BlockedStatus when ops raises ModelError reading the secret."""
        with patch.object(
            charm_harness.charm.model,
            "get_secret",
            side_effect=ops.ModelError("permission denied"),
        ):
            charm_harness.charm.on.update_status.emit()

        assert isinstance(charm_harness.charm.unit.status, ops.BlockedStatus)
        assert "encryption-keys-secret-id" in charm_harness.charm.unit.status.message
