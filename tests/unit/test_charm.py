# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

"""Run unit tests."""

import unittest

import ops
import ops.testing

from charm import DatahubK8SOperatorCharm


class TestCharm(unittest.TestCase):
    """Test class."""

    def setUp(self):
        """Sets up environment for test cases."""
        self.harness = ops.testing.Harness(DatahubK8SOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_noop(self):
        """Test will be replaced in the tests update."""
        self.assertEqual(1, 1)
