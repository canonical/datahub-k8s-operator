# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for structured_config.py."""

import pytest

import literals
from structured_config import parse_kafka_topic_retention


def test_unset_option_gives_the_defaults():
    """With no overrides every managed topic gets a bounded retention and no size limit."""
    assert parse_kafka_topic_retention(None) == literals.KAFKA_RETENTION_DEFAULTS
    assert parse_kafka_topic_retention("{}") == literals.KAFKA_RETENTION_DEFAULTS


def test_overrides_merge_per_topic_and_setting():
    """A setting replaces only that setting of that topic. Other topics keep the defaults."""
    parsed = parse_kafka_topic_retention(
        '{"usage-event": {"retention-bytes": 1024}, "platform-event": {"retention-ms": -1}}'
    )
    assert parsed["usage-event"] == {"retention-ms": literals.KAFKA_RETENTION_MS_DEFAULT, "retention-bytes": 1024}
    assert parsed["platform-event"] == {"retention-ms": -1}
    assert parsed["metadata-change-proposal"] == {"retention-ms": literals.KAFKA_RETENTION_MS_DEFAULT}


@pytest.mark.parametrize(
    "value, message",
    [
        ("not-json", "Expecting value"),
        ("[]", "must be a JSON object"),
        ('{"upgrade-history": {"retention-ms": 1}}', "unknown topics ['upgrade-history']"),
        ('{"usage-event": 5}', "'usage-event' must be a JSON object"),
        ('{"usage-event": {"retention.ms": 5}}', "unknown settings ['retention.ms']"),
        ('{"usage-event": {"retention-ms": "5"}}', "must be an integer of -1 or more"),
        ('{"usage-event": {"retention-ms": true}}', "must be an integer of -1 or more"),
        ('{"usage-event": {"retention-bytes": -2}}', "must be an integer of -1 or more"),
    ],
)
def test_invalid_values_are_rejected(value, message):
    """The error names what is wrong, so the blocked status tells the operator what to fix."""
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        parse_kafka_topic_retention(value)
