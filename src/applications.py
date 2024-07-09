# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub applications."""

import abc
from dataclasses import dataclass
from typing import Dict

from ops import CharmBase
from structured_config import CharmConfig


def _kafka_topic_names(prefix: str) -> Dict[str, str]:
    """Compiles the environment variables for Kafka topic names.

    Args:
        prefix: Prefix to use for the topic names.
    """
    # Ref: https://github.com/datahub-project/datahub/blob/master/docs/how/kafka-config.md#topic-configuration
    # These will be shared across all applications unless a conflict is detected.
    # In that case, each application will have environment variables strictly for itself.
    default_names = {
        "METADATA_CHANGE_PROPOSAL_TOPIC_NAME": "MetadataChangeProposal_v1",
        "FAILED_METADATA_CHANGE_PROPOSAL_TOPIC_NAME": "FailedMetadataChangeProposal_v1",
        "METADATA_CHANGE_LOG_VERSIONED_TOPIC_NAME": "MetadataChangeLog_Versioned_v1",
        "METADATA_CHANGE_LOG_TIMESERIES_TOPIC_NAME": "MetadataChangeLog_Timeseries_v1",
        "PLATFORM_EVENT_TOPIC_NAME": "PlatformEvent_v1",
        "DATAHUB_USAGE_EVENT_NAME": "DataHubUsageEvent_v1",
    }
    default_names["DATAHUB_TRACKING_TOPIC"] = default_names["DATAHUB_USAGE_EVENT_NAME"]

    if prefix == "":
        return default_names

    names = {f"{prefix}_{k}": v for (k, v) in default_names.items()}
    return names


@dataclass
class ApplicationContext:
    """Context manager for application methods."""

    charm: CharmBase
    config: CharmConfig


class AbstractApplication(abc.ABC):
    """Base class for applications."""

    name: str
    command: str

    @classmethod
    @abc.abstractmethod
    def is_enabled(cls, context: ApplicationContext) -> bool:
        """Determine if the application should be enabled using configs and context clues.

        Args:
            context: Context for the application.
        """
        pass

    @classmethod
    @abc.abstractmethod
    def compile_environment(cls, context: ApplicationContext) -> Dict:
        """Compile application specific environment variables from the context.

        Args:
            context: Context for the application.
        """
        pass


class ActionsApplication(AbstractApplication):
    """Application class for DataHub Actions."""

    name = "datahub-actions"
    command = "/bin/sh -c /start_datahub_actions.sh"

    @classmethod
    def is_enabled(cls, context: ApplicationContext) -> bool:
        """Determine if the application should be enabled using configs and context clues.

        Args:
            context: Context for the application.
        """
        return True

    @classmethod
    def compile_environment(cls, context: ApplicationContext) -> Dict:
        """Compile application specific environment variables from the context.

        Args:
            context: Context for the application.
        """
        env = {}
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class FrontendApplication(AbstractApplication):
    """Application class for DataHub Frontend."""

    name = "datahub-frontend"
    command = "/bin/sh -c /start.sh"

    @classmethod
    def is_enabled(cls, context: ApplicationContext) -> bool:
        """Determine if the application should be enabled using configs and context clues.

        Args:
            context: Context for the application.
        """
        return True

    @classmethod
    def compile_environment(cls, context: ApplicationContext) -> Dict:
        """Compile application specific environment variables from the context.

        Args:
            context: Context for the application.
        """
        env = {
            "DATAHUB_SECRET": context.config.datahub_secret_key,
            "KAFKA_BOOTSTRAP_SERVER": context.charm._state.kafka_connection["bootstrap_server"],
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class GMSApplication(AbstractApplication):
    """Application class for DataHub GMS."""

    name = "datahub-gms"
    command = "/bin/sh -c /datahub/datahub-gms/scripts/start.sh"

    @classmethod
    def is_enabled(cls, context: ApplicationContext) -> bool:
        """Determine if the application should be enabled using configs and context clues.

        Args:
            context: Context for the application.
        """
        return True

    @classmethod
    def compile_environment(cls, context: ApplicationContext) -> Dict:
        """Compile application specific environment variables from the context.

        Args:
            context: Context for the application.
        """
        env = {
            "KAFKA_BOOTSTRAP_SERVER": context.charm._state.kafka_connection["bootstrap_server"],
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class MAEConsumerApplication(AbstractApplication):
    """Application class for DataHub MAE Consumer."""

    name = "datahub-mae-consumer"
    command = "/bin/sh -c /datahub/datahub-mae-consumer/scripts/start.sh"

    @classmethod
    def is_enabled(cls, context: ApplicationContext) -> bool:
        """Determine if the application should be enabled using configs and context clues.

        Args:
            context: Context for the application.
        """
        return context.config.enable_mae_consumer

    @classmethod
    def compile_environment(cls, context: ApplicationContext) -> Dict:
        """Compile application specific environment variables from the context.

        Args:
            context: Context for the application.
        """
        env = {
            "KAFKA_BOOTSTRAP_SERVER": context.charm._state.kafka_connection["bootstrap_server"],
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class MCEConsumerApplication(AbstractApplication):
    """Application class for DataHub MCE Consumer."""

    name = "datahub-mce-consumer"
    command = "/bin/sh -c /datahub/datahub-mce-consumer/scripts/start.sh"

    @classmethod
    def is_enabled(cls, context: ApplicationContext) -> bool:
        """Determine if the application should be enabled using configs and context clues.

        Args:
            context: Context for the application.
        """
        return context.config.enable_mce_consumer

    @classmethod
    def compile_environment(cls, context: ApplicationContext) -> Dict:
        """Compile application specific environment variables from the context.

        Args:
            context: Context for the application.
        """
        env = {
            "KAFKA_BOOTSTRAP_SERVER": context.charm._state.kafka_connection["bootstrap_server"],
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env
