# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the charm."""

import json
import logging
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator

logger = logging.getLogger(__name__)

_DEFAULT_PATTERN = {"allow": [".*"], "deny": []}
_TRINO_PATTERNS_DEFAULT = json.dumps(
    {
        "schema-pattern": _DEFAULT_PATTERN,
        "table-pattern": _DEFAULT_PATTERN,
        "view-pattern": _DEFAULT_PATTERN,
    }
)


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
    """

    auth_verbose_logging: bool = False
    encryption_keys_secret_id: str
    use_play_cache_session_store: bool
    kafka_topic_prefix: Optional[str] = None
    opensearch_index_prefix: Optional[str] = None
    trino_patterns: str = _TRINO_PATTERNS_DEFAULT

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
