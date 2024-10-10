# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

"""Run unit tests."""

import unittest
from unittest.mock import MagicMock

import utils


class TestUtils(unittest.TestCase):
    def test_push_contents_to_file_str(self):
        container = MagicMock()
        contents = "string"
        dest = "/foo"
        make_dirs = True
        perm = 0o644
        utils.push_contents_to_file(container, contents, dest, perm)
        container.push.assert_called_once_with(dest, contents, make_dirs=make_dirs, permissions=perm)

    def test_push_contents_to_file_bytes(self):
        container = MagicMock()
        contents = b"bytes"
        dest = "/foo"
        make_dirs = True
        perm = 0o644
        utils.push_contents_to_file(container, contents, dest, perm)
        container.push.assert_called_once_with(dest, contents, make_dirs=make_dirs, permissions=perm)

    def test_push_contents_to_file_int(self):
        container = MagicMock()
        contents = 123
        dest = "/foo"
        perm = 0o644

        with self.assertRaises(ValueError) as context:
            utils.push_contents_to_file(container, contents, dest, perm)

        self.assertTrue("invalid content type" in str(context.exception))

    def test_push_contents_to_file_none(self):
        container = MagicMock()
        contents = None
        dest = "/foo"
        perm = 0o644

        with self.assertRaises(ValueError) as context:
            utils.push_contents_to_file(container, contents, dest, perm)

        self.assertTrue("invalid content type" in str(context.exception))
