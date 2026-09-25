# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common exceptions."""


class InitializationFailedError(Exception):
    """Raised when an initialization script failed to run."""


class ImproperSecretError(Exception):
    """Raised when there is an issue with a Juju secret."""


class UnreadyStateError(Exception):
    """Raised when the charm is not ready due to its state."""


class BackendDriftError(Exception):
    """Raised when a backend lacks something the SystemUpdate job creates."""


class BackendRestoringError(Exception):
    """Raised while the SystemUpdate job runs to restore the backends."""


class BackendRetryingError(Exception):
    """Raised while pebble waits to rerun a SystemUpdate job that failed."""


class BackendUnreachableError(Exception):
    """Raised when a backend cannot be queried."""


class BadLogicError(Exception):
    """Raised when a state deemed impossible is reached.

    This is to be used in place of `assert False` as
    our linters are not fond of asserts outside of tests.
    """


class SetupFailedError(Exception):
    """Raised during tests when test setup fails."""
