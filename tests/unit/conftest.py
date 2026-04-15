# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared fixtures for unit tests."""

import pytest
from ops.testing import Harness

from charm import DatahubK8SOperatorCharm

_SECRET_ID = "secret:test-encryption-secret-id"  # nosec B105


@pytest.fixture
def charm_harness():
    """Yield a configured Harness for DatahubK8SOperatorCharm."""
    h = Harness(DatahubK8SOperatorCharm)
    h.update_config({"encryption-keys-secret-id": _SECRET_ID})
    h.begin()
    yield h
    h.cleanup()
