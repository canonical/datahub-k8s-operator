# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the charm."""

import logging
from enum import Enum

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import field_validator

logger = logging.getLogger(__name__)


class LogLevelType(str, Enum):
    """Enum for the `log-level` field."""

    INFO = "INFO"
    DEBUG = "DEBUG"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    log_level: LogLevelType
    enable_mae_consumer: bool
    enable_mce_consumer: bool
    kafka_topic_prefix: str
    opensearch_index_prefix: str
    datahub_gms_secret_encryption_key: str
    datahub_frontend_secret_key: str

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
