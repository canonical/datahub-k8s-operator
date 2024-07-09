# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common exceptions."""


class InitializationFailedError(Exception):
    """Raised when an initialization script failed to run."""

    pass


class ImproperSecretError(Exception):
    """Raised when there is an issue with a Juju secret."""

    pass


class UnreadyStateError(Exception):
    """Raised when the charm is not ready due to its state."""
