# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub services."""

import abc
from dataclasses import dataclass
from typing import Dict, Optional

from ops import CharmBase
from structured_config import CharmConfig


def _kafka_topic_names(prefix: str) -> Optional[Dict[str, str]]:
    """Compiles the environment variables for Kafka topic names.

    Args:
        prefix: Prefix to use for the topic names.
    """
    # Ref: https://github.com/datahub-project/datahub/blob/master/docs/how/kafka-config.md#topic-configuration
    # These will be shared across all services unless a conflict is detected.
    # In that case, each service will have environment variables strictly for itself.
    default_names = {
        "METADATA_CHANGE_PROPOSAL_TOPIC_NAME": "MetadataChangeProposal_v1",
        "FAILED_METADATA_CHANGE_PROPOSAL_TOPIC_NAME": "FailedMetadataChangeProposal_v1",
        "METADATA_CHANGE_LOG_VERSIONED_TOPIC_NAME": "MetadataChangeLog_Versioned_v1",
        "METADATA_CHANGE_LOG_TIMESERIES_TOPIC_NAME": "MetadataChangeLog_Timeseries_v1",
        "PLATFORM_EVENT_TOPIC_NAME": "PlatformEvent_v1",
        "DATAHUB_UPGRADE_HISTORY_TOPIC_NAME": "DataHubUpgradeHistory_v1",
        "DATAHUB_USAGE_EVENT_NAME": "DataHubUsageEvent_v1",
    }
    default_names["DATAHUB_TRACKING_TOPIC"] = default_names["DATAHUB_USAGE_EVENT_NAME"]

    if prefix == "":
        return default_names

    names = {k: f"{prefix}_{v}" for (k, v) in default_names.items()}
    return names


# TODO (mertalpt): Remove this class: 'charm.config' is 'config'
@dataclass
class ServiceContext:
    """Context manager for service methods."""

    charm: CharmBase
    config: CharmConfig


class AbstractService(abc.ABC):
    """Base class for services."""

    name: str
    command: str

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        return False

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.
        """
        return None


class ActionsService(AbstractService):
    """Service class for DataHub Actions."""

    name = "datahub-actions"
    command = "/bin/sh -c /start_datahub_actions.sh"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        kafka_conn = context.charm._state.kafka_connection
        
        return kafka_conn and GMSService.is_enabled(context)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.
        """
        if not cls.is_enabled(context):
            return None

        kafka_conn = context.charm._state.kafka_connection

        env = {
            "DATAHUB_GMS_PROTOCOL": "http",
            # TODO (mertalpt): This changes when we split services into multiple charms.
            "DATAHUB_GMS_HOST": "localhost",
            "DATAHUB_GMS_PORT": "8080",
            "KAFKA_BOOTSTRAP_SERVER": kafka_conn["bootstrap_server"],
            # TODO (mertalpt): This changes when we split services into multiple charms or implement external registry.
            "SCHEMA_REGISTRY_URL": "http://localhost:8080/schema-registry/api/",
            "KAFKA_AUTO_OFFSET_POLICY": "latest",
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class FrontendService(AbstractService):
    """Service class for DataHub Frontend."""

    name = "datahub-frontend"
    command = "/bin/sh -c /start.sh"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection

        return kafka_conn and os_conn and GMSService.is_enabled(context)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.
        """
        if not cls.is_enabled(context):
            return None

        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection

        env = {
            # TODO (mertalpt): To be implemented with to o11y update.
            "ENABLE_PROMETHEUS": "false",
            # TODO (mertalpt): This changes when we split services into multiple charms.
            "DATAHUB_GMS_HOST": "localhost",
            "DATAHUB_GMS_PORT": "8080",
            "DATAHUB_SECRET": context.config.datahub_frontend_secret_key,
            "DATAHUB_APP_VERSION": "1.0",
            "DATAHUB_PLAY_MEM_BUFFER_SIZE": "10MB",
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "KAFKA_BOOTSTRAP_SERVER": kafka_conn["bootstrap_server"],
            "ENFORCE_VALID_EMAIL": "true",
            "KAFKA_PRODUCER_COMPRESSION_TYPE": "none",
            "KAFKA_PRODUCER_MAX_REQUEST_SIZE": "5242880",
            "KAFKA_CONSUMER_MAX_PARTITION_FETCH_BYTES": "5242880",
            "ELASTIC_CLIENT_HOST": os_conn["host"],
            "ELASTIC_CLIENT_PORT": os_conn["port"],
            # Current version of the Opensearch VM charm requires TLS by default, so this should be a safe default.
            "ELASTIC_CLIENT_USE_SSL": "true",
            "ELASTIC_CLIENT_USERNAME": os_conn["username"],
            "ELASTIC_CLIENT_PASSWORD": os_conn["password"],
            "ELASTIC_INDEX_PREFIX": context.config.opensearch_index_prefix,
            "AUTH_SESSION_TTL_HOURS": "24",
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class GMSService(AbstractService):
    """Service class for DataHub GMS."""

    name = "datahub-gms"
    command = "/bin/sh -c /datahub/datahub-gms/scripts/start.sh"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        db_conn = context.charm._state.database_connection
        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection
        return (
            db_conn.get("initialized")
            and kafka_conn.get("initialized")
            and os_conn.get("initialized")
        )

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.
        """
        if not cls.is_enabled(context):
            return None

        db_conn = context.charm._state.database_connection
        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection

        env = {
            "EBEAN_DATASOURCE_PORT": db_conn["port"],
            "SHOW_SEARCH_FILTERS_V2": "true",
            "SHOW_BROWSE_V2": "true",
            "BACKFILL_BROWSE_PATHS_V2": "true",
            # TODO (mertalpt): To be implemented with to o11y update.
            "ENABLE_PROMETHEUS": "false",
            # TODO (mertalpt): To be changed when standalone consumers are fully implemented.
            "MCE_CONSUMER_ENABLED": "true",
            "MAE_CONSUMER_ENABLED": "true",
            "PE_CONSUMER_ENABLED": "true",
            "ENTITY_REGISTRY_CONFIG_PATH": "/datahub/datahub-gms/resources/entity-registry.yml",
            "DATAHUB_ANALYTICS_ENABLED": "true",
            # PostgreSQL credentials.
            "EBEAN_DATASOURCE_USERNAME": db_conn["username"],
            "EBEAN_DATASOURCE_PASSWORD": db_conn["password"],
            "EBEAN_DATASOURCE_HOST": f"{db_conn['host']}:{db_conn['port']}",
            "EBEAN_DATASOURCE_URL": f"jdbc:postgresql://{db_conn['host']}:{db_conn['port']}/{db_conn['dbname']}",
            "EBEAN_DATASOURCE_DRIVER": "org.postgresql.Driver",
            "KAFKA_BOOTSTRAP_SERVER": kafka_conn["bootstrap_server"],
            "KAFKA_PRODUCER_COMPRESSION_TYPE": "none",
            "KAFKA_CONSUMER_STOP_ON_DESERIALIZATION_ERROR": "true",
            "KAFKA_PRODUCER_MAX_REQUEST_SIZE": "5242880",
            "KAFKA_CONSUMER_MAX_PARTITION_FETCH_BYTES": "5242880",
            # TODO (mertalpt): If we ever implement external registry (e.g. Karapace), this needs to change.
            "KAFKA_SCHEMAREGISTRY_URL": "http://localhost:8080/schema-registry/api/",
            "SCHEMA_REGISTRY_TYPE": "INTERNAL",
            "ELASTICSEARCH_HOST": os_conn["host"],
            "ELASTICSEARCH_PORT": os_conn["port"],
            "SKIP_ELASTICSEARCH_CHECK": "true",
            # TODO (mertalpt): GMS cannot connect to OS due to unverified certificates.
            # Potential solution in: https://github.com/datahub-project/datahub/issues/8433
            "ELASTICSEARCH_USE_SSL": "true",
            "ELASTICSEARCH_SSL_TRUSTSTORE_FILE": "/truststore.p12",
            "ELASTICSEARCH_SSL_TRUSTSTORE_TYPE": "PKCS12",
            "ELASTICSEARCH_SSL_TRUSTSTORE_PASSWORD": "foobar",
            "JAVA_OPTS": "-Djavax.net.ssl.trustStore=/truststore.p12 -Djavax.net.ssl.trustStoreType=PKCS12 -Djavax.net.ssl.trustStorePassword=foobar",
            "ELASTICSEARCH_USERNAME": os_conn["username"],
            "ELASTICSEARCH_PASSWORD": os_conn["password"],
            "INDEX_PREFIX": context.config.opensearch_index_prefix,
            "GRAPH_SERVICE_IMPL": "elasticsearch",
            "UI_INGESTION_ENABLED": "true",
            "SECRET_SERVICE_ENCRYPTION_KEY": context.config.datahub_gms_secret_encryption_key,
            "ENTITY_SERVICE_ENABLE_RETENTION": "false",
            "ELASTICSEARCH_QUERY_MAX_TERM_BUCKET_SIZE": "20",
            "ELASTICSEARCH_QUERY_EXACT_MATCH_EXCLUSIVE": "false",
            "ELASTICSEARCH_QUERY_EXACT_MATCH_WITH_PREFIX": "true",
            "ELASTICSEARCH_QUERY_EXACT_MATCH_FACTOR": "2",
            "ELASTICSEARCH_QUERY_EXACT_MATCH_PREFIX_FACTOR": "1.6",
            "ELASTICSEARCH_QUERY_EXACT_MATCH_CASE_FACTOR": "0.7",
            "ELASTICSEARCH_QUERY_EXACT_MATCH_ENABLE_STRUCTURED": "true",
            "ELASTICSEARCH_SEARCH_GRAPH_TIMEOUT_SECONDS": "50",
            "ELASTICSEARCH_SEARCH_GRAPH_BATCH_SIZE": "1000",
            "ELASTICSEARCH_SEARCH_GRAPH_MAX_RESULT": "10000",
            "SEARCH_SERVICE_ENABLE_CACHE": "false",
            "LINEAGE_SEARCH_CACHE_ENABLED": "false",
            "ELASTICSEARCH_INDEX_BUILDER_MAPPINGS_REINDEX": "true",
            "ELASTICSEARCH_INDEX_BUILDER_SETTINGS_REINDEX": "true",
            "ALWAYS_EMIT_CHANGE_LOG": "false",
            "GRAPH_SERVICE_DIFF_MODE_ENABLED": "true",
            "GRAPHQL_QUERY_INTROSPECTION_ENABLED": "true",
        }
        kafka_env = _kafka_topic_names(context.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class MAEConsumerService(AbstractService):
    """Service class for DataHub MAE Consumer."""

    name = "datahub-mae-consumer"
    command = "/bin/sh -c /datahub/datahub-mae-consumer/scripts/start.sh"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        return context.config.enable_mae_consumer

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.
        """
        # TODO (mertalpt): Implement with standalone consumers.
        return None


class MCEConsumerService(AbstractService):
    """Service class for DataHub MCE Consumer."""

    name = "datahub-mce-consumer"
    command = "/bin/sh -c /datahub/datahub-mce-consumer/scripts/start.sh"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        return context.config.enable_mce_consumer

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.
        """
        # TODO (mertalpt): Implement with standalone consumers.
        return None


class OpensearchSetupService(AbstractService):
    """Service class for Opensearch setup job."""

    name = "datahub-opensearch-setup"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        return True


class KafkaSetupService(AbstractService):
    """Service class for Kafka setup job."""

    name = "datahub-kafka-setup"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        return True


class PostgresqlSetupService(AbstractService):
    """Service class for Postgresql setup job."""

    name = "datahub-postgresql-setup"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.
        """
        return True
