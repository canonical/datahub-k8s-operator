# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Fixtures for charm tests."""

import pytest


def pytest_addoption(parser: pytest.Parser):
    """Parse additional pytest options.

    Args:
        parser: pytest command line parser.
    """
    # The prebuilt charm file.
    parser.addoption("--charm-file", action="append", default=[])

    # Locally built rock OCI resources.
    parser.addoption("--datahub-actions-image", action="store")
    parser.addoption("--datahub-frontend-image", action="store")
    parser.addoption("--datahub-gms-image", action="store")

    # Compatibility with canonical/operator-workflows CI after `jubilant` migration.
    parser.addoption("--model", action="store", default=None)
    parser.addoption("--keep-models", action="store_true", default=False)
    parser.addoption("--series", action="store", default=None)
