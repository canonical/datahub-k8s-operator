# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the charm."""

import logging
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration.

    Attributes:
        encryption_keys_secret_id: Juju secret ID to use for secret keys.
        oidc_secret_id: Juju secret ID to enable SSO.
        kafka_topic_prefix: Prefix to use for Kafka topic names.
        opensearch_index_prefix: Prefix to use for Opensearch indexes.
    """

    encryption_keys_secret_id: str
    oidc_secret_id: Optional[str] = None
    kafka_topic_prefix: Optional[str] = None
    opensearch_index_prefix: Optional[str] = None

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
