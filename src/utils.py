# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of helper methods."""

import logging
import os
import random
import string

logger = logging.getLogger(__name__)


def get_abs_path(*relative_path: str) -> str:
    """Get the absolute path for a file relative to the charm's root directory.

    Args:
        relative_path: Tuple of path segments relative to the charm's root directory.

    Returns:
        Absolute path of the given file or directory under the charm's root directory.
    """
    charm_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
    if not relative_path:
        return charm_dir
    return os.path.join(charm_dir, *relative_path)


def generate_secret(length: int = 32) -> str:
    """Generate a secure secret string.

    Args:
        length: Length of the secret to create, must be greater than four.

    Returns:
        An alphanumeric string of the specified length.
    """
    if length < 4:
        raise ValueError("Specific secret length '%d' is too short.", length)

    chars = []

    # Lower case
    chars.append(random.choice(string.ascii_lowercase))

    # Upper case
    chars.append(random.choice(string.ascii_uppercase))

    # Digit
    chars.append(random.choice(string.digits))

    # Padding
    chars.extend(random.choices(string.ascii_letters + string.digits, length - 3))

    random.shuffle(chars)
    secret = "".join(chars)

    return secret
