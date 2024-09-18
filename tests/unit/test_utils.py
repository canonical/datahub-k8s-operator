# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

"""Run unit tests."""

import unittest
from unittest.mock import MagicMock

import utils


class TestUtils(unittest.TestCase):
    """Unit tests for the utils.py module."""

    def test_push_contents_to_file_str(self):
        """Test if push_contents_to_file accepts strings."""
        container = MagicMock()
        contents = "string"
        dest = "/foo"
        make_dirs = True
        perm = 0o644
        utils.push_contents_to_file(container, contents, dest, perm)
        container.push.assert_called_once_with(dest, contents, make_dirs=make_dirs, permissions=perm)

    def test_push_contents_to_file_bytes(self):
        """Test if push_contents_to_file accepts bytes."""
        container = MagicMock()
        contents = b"bytes"
        dest = "/foo"
        make_dirs = True
        perm = 0o644
        utils.push_contents_to_file(container, contents, dest, perm)
        container.push.assert_called_once_with(dest, contents, make_dirs=make_dirs, permissions=perm)

    def test_push_contents_to_file_int(self):
        """Test if push_contents_to_file does not accept an invalid type."""
        container = MagicMock()
        contents = 123
        dest = "/foo"
        perm = 0o644

        with self.assertRaises(ValueError) as context:
            utils.push_contents_to_file(container, contents, dest, perm)

        self.assertTrue("invalid content type" in str(context.exception))

    def test_push_contents_to_file_none(self):
        """Test if push_contents_to_file accepts None."""
        container = MagicMock()
        contents = None
        dest = "/foo"
        perm = 0o644

        with self.assertRaises(ValueError) as context:
            utils.push_contents_to_file(container, contents, dest, perm)

        self.assertTrue("invalid content type" in str(context.exception))
