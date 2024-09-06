# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Common exceptions."""


class InitializationFailedError(Exception):
    """Raised when an initialization script failed to run."""


class ImproperSecretError(Exception):
    """Raised when there is an issue with a Juju secret."""


class UnreadyStateError(Exception):
    """Raised when the charm is not ready due to its state."""


class BadLogicError(Exception):
    """Raised when a state deemed impossible is reached.

    This is to be used in place of `assert False` as
    our linters are not fond of asserts outside of tests.
    """
