# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the charm."""

import json
import logging
from typing import Dict, Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator

import literals

logger = logging.getLogger(__name__)

_DEFAULT_PATTERN = {"allow": [".*"], "deny": []}
_TRINO_PATTERNS_DEFAULT = json.dumps(
    {
        "schema-pattern": _DEFAULT_PATTERN,
        "table-pattern": _DEFAULT_PATTERN,
        "view-pattern": _DEFAULT_PATTERN,
    }
)

_KAFKA_TOPIC_RETENTION_DEFAULT = json.dumps(literals.KAFKA_RETENTION_DEFAULTS)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration.

    Attributes:
        auth_verbose_logging: Enables verbose authentication logging.
        encryption_keys_secret_id: Juju secret ID to use for secret keys.
        use_play_cache_session_store: Flag to determine if Play cache will be used for
            session store instead of having browser based OIDC cookies.
        kafka_topic_prefix: Prefix to use for Kafka topic names.
        opensearch_index_prefix: Prefix to use for Opensearch indexes.
        trino_patterns: JSON string with schema, table, and view filter patterns.
        kafka_topic_retention: JSON string with retention overrides per Kafka topic.
    """

    auth_verbose_logging: bool = False
    encryption_keys_secret_id: str
    use_play_cache_session_store: bool
    kafka_topic_prefix: Optional[str] = None
    opensearch_index_prefix: Optional[str] = None
    trino_patterns: str = _TRINO_PATTERNS_DEFAULT
    kafka_topic_retention: Optional[str] = _KAFKA_TOPIC_RETENTION_DEFAULT

    @field_validator("*", mode="before")
    @classmethod
    def blank_string(cls, value):
        """Check for empty strings.

        Args:
            value: configuration value

        Returns:
            None in place of empty string or value
        """
        if value == "":
            return None
        return value


def parse_kafka_topic_retention(value: Optional[str]) -> Dict[str, Dict[str, int]]:
    """Merge the `kafka-topic-retention` option over the charm's retention defaults.

    Args:
        value: The option's JSON value. None or empty means no overrides.

    Returns:
        For each topic in `literals.KAFKA_RETENTION_TOPICS`, the retention settings to apply,
        keyed like the option (`retention-ms`, `retention-bytes`).

    Raises:
        ValueError: If the value is not a JSON object of known topics and settings with
            integer values of -1 or more.
    """
    try:
        overrides = json.loads(value or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from None
    if not isinstance(overrides, dict):
        raise ValueError("must be a JSON object")
    unknown = sorted(set(overrides) - set(literals.KAFKA_RETENTION_TOPICS))
    if unknown:
        raise ValueError(f"unknown topics {unknown}, expected some of {sorted(literals.KAFKA_RETENTION_TOPICS)}")

    merged = {}
    for topic, defaults in literals.KAFKA_RETENTION_DEFAULTS.items():
        settings = overrides.get(topic, {})
        if not isinstance(settings, dict):
            raise ValueError(f"'{topic}' must be a JSON object")
        unknown = sorted(set(settings) - set(literals.KAFKA_RETENTION_KEYS))
        if unknown:
            raise ValueError(f"unknown settings {unknown} for '{topic}'")
        for key, number in settings.items():
            # bool is a subclass of int, so `true` would pass as 1 without the first check.
            if isinstance(number, bool) or not isinstance(number, int) or number < -1:
                raise ValueError(f"'{topic}' {key} must be an integer of -1 or more")
        merged[topic] = {**defaults, **settings}
    return merged
