# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of helper methods."""

import logging
import os
import random
import secrets
import string
from typing import Any, Dict, List, Optional, Tuple

from ops import pebble

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


def push_contents_to_file(container: Any, contents: pebble._IOSource, dest_path: str, permissions: int) -> None:
    """Push a string to the path in the given container as a file.

    Args:
        container: The container to which the script should be pushed.
        contents: String that will form the contents of the pushed file.
        dest_path: Absolute path to place the file in, including the file name.
        permissions: Permissions to use for the file.

    Raises:
        ValueError: If type of 'contents' is invalid.
    """
    # `push` uses a custom type, the following type checks for that type as defined now.
    # This is prone to breaking if the type definition changes but it should be easy to
    # catch and fix it quickly.
    is_valid_type = False
    if isinstance(contents, (str, bytes)):
        is_valid_type = True
    # Type check for the protocol component.
    elif hasattr(contents, "read") and hasattr(contents, "write") and hasattr(contents, "__enter__"):
        is_valid_type = True

    if not is_valid_type:
        raise ValueError(f"invalid content type: '{type(contents)}'")

    container.push(dest_path, contents, make_dirs=True, permissions=permissions)


def push_file(container: Any, src_path: Tuple[str, ...], dest_path: str, permissions: int) -> None:
    """Push files from the charm directory to the path in the given container.

    Args:
        container: The container to which the script should be pushed.
        src_path: Path of the source file given relative to the repository root.
        dest_path: Absolute path to place the file in, including the file name.
        permissions: Permissions to use for the file.
    """
    src_abs_path = get_abs_path(*src_path)
    with open(src_abs_path, "r") as fp:
        contents = fp.read()
    push_contents_to_file(container, contents, dest_path, permissions)


def generate_secret(length: int = 32) -> str:
    """Generate a secure secret string.

    Args:
        length: Length of the secret to create, must be greater than four.

    Returns:
        An alphanumeric string of the specified length.

    Raises:
        ValueError: If requested length is shorter than 4.
    """
    if length < 4:
        raise ValueError(f"Specific secret length '{length}' is too short.")

    chars = []

    # Lower case
    chars.append(secrets.choice(string.ascii_lowercase))

    # Upper case
    chars.append(secrets.choice(string.ascii_uppercase))

    # Digit
    chars.append(secrets.choice(string.digits))

    # Padding
    chars.extend((secrets.choice(string.ascii_letters + string.digits) for _ in range(length - 3)))

    random.shuffle(chars)
    secret = "".join(chars)

    return secret


def split_certificates(certificates: str) -> List[str]:
    """Split a PEM formatted string into component certificates.

    Split a PEM formatted string of potentially multiple certificates
    into individual certificates.

    Input string is not validated for correctness.

    Args:
        certificates: PEM formatted string of certificates.

    Returns:
        A list of strings where each string is a PEM formatted certificate.
    """
    certs = [
        cert.strip() + "\n-----END CERTIFICATE-----"
        for cert in certificates.split("-----END CERTIFICATE-----")
        if cert.strip()
    ]
    return certs


def get_from_optional_dict(dictionary: Optional[Dict], key: str) -> Optional[Any]:
    """If it exists, get the value of a key from an optional dictionary.

    Args:
        dictionary: Optional dictionary to get the value from.
        key: The key for which the value should be retrieved.

    Returns:
        The value if both the dictionary and the value exists.

    Raises:
        ValueError: If the input 'dictionary' is not of type 'dict'.
    """
    if dictionary is None:
        return None

    if not isinstance(dictionary, dict):
        raise ValueError(f"type '{type(dictionary)}' is not 'dict'")

    return dictionary.get(key)
