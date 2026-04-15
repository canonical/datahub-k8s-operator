# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared fixtures for unit tests."""

from pathlib import Path

import pytest
from ops import testing

from charm import DatahubK8SOperatorCharm

_SECRET_ID = "secret:test-encryption-secret-id"  # nosec B105


@pytest.fixture
def charm_ctx() -> testing.Context[DatahubK8SOperatorCharm]:
    """Build a charm test context for state-transition tests."""
    charm_root = Path(__file__).resolve().parents[2]
    return testing.Context(DatahubK8SOperatorCharm, charm_root=charm_root)


@pytest.fixture
def base_state() -> testing.State:
    """Provide minimum valid config shared by tests."""
    return testing.State(config={"encryption-keys-secret-id": _SECRET_ID})
