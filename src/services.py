# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub services."""

# pylint: disable=C0302

# TODO (mertalpt): Convert the module to a package and split up services.

# Some General Notes
# 1. `charm._state` does not behave in the standard Python way, you cannot get a reference
#    to an object inside it and update it. For lasting updates you need to an assignment with
#    the updated object.
# 2. `process.wait()` causes some scripts (i.e. datahub-upgrade) to get stuck, which is why we use
#    `process.wait_output()` even though we do not read the output. It does not have the same
#    problem for some reason.
# 3. Flags in `charm._state` have three states: True, False, None. Logic for them is very specific
#    depending on the point in the program lifecycle and at certain places, explicit checks with
#   `is` is required over general truthiness checks.

import abc
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional
from urllib.parse import urlparse

import exceptions
import literals
import utils

if TYPE_CHECKING:
    from charm import DatahubK8SOperatorCharm  # noqa

logger = logging.getLogger(__name__)


def _kafka_topic_names(prefix: str) -> Dict[str, str]:
    """Compiles the environment variables for Kafka topic names.

    Args:
        prefix: Prefix to use for the topic names.

    Returns:
        A dictionary that maps topic names for corresponding environment variables.
    """
    # Ref: https://github.com/datahub-project/datahub/blob/master/docs/how/kafka-config.md#topic-configuration  # noqa: W505
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

    if not prefix:
        return default_names

    names = {k: f"{prefix}_{v}" for (k, v) in default_names.items()}
    return names


@dataclass
class ServiceContext:
    """Context manager for service methods.

    Attributes:
        charm: Charm instance that runs the services.
    """

    # We can use the charm object straight up instead of having a 'ServiceContext' class.
    # But, the first implementation used a 'ServiceContext'
    # and I would like to leave an opening for future changes.
    charm: "DatahubK8SOperatorCharm"


class AbstractService(abc.ABC):
    """Base class for services.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name: str
    command: str
    healthcheck: Optional[Dict]

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        return False

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        return False

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        return None

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.
        """
        return False


class ActionsService(AbstractService):
    """Service class for DataHub Actions.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-actions"
    command = "/bin/sh -c /start_datahub_actions.sh"
    healthcheck = None

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        return is_ready

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (
            context.charm._state.ran_upgrade,
            utils.get_from_optional_dict(context.charm._state.kafka_connection, "initialized"),
            GMSService.is_enabled(context),
        )
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        if not cls.is_enabled(context):
            return None

        kafka_conn = context.charm._state.kafka_connection

        env = {
            "DATAHUB_TELEMETRY_ENABLED": "false",
            "DATAHUB_GMS_PROTOCOL": "http",
            # TODO (mertalpt): This changes when we split services into multiple charms.
            "DATAHUB_GMS_HOST": "localhost",
            "DATAHUB_GMS_PORT": "8080",
            "KAFKA_BOOTSTRAP_SERVER": kafka_conn["bootstrap_server"],
            # TODO (mertalpt): This changes when we split services into multiple charms
            # or implement external registry.
            "SCHEMA_REGISTRY_URL": "http://localhost:8080/schema-registry/api/",
            "KAFKA_AUTO_OFFSET_POLICY": "latest",
            "KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "KAFKA_PROPERTIES_SASL_USERNAME": kafka_conn["username"],
            "KAFKA_PROPERTIES_SASL_PASSWORD": kafka_conn["password"],
        }
        kafka_env = _kafka_topic_names(context.charm.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env


class FrontendService(AbstractService):
    """Service class for DataHub Frontend.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-frontend"
    command = "/bin/sh -c /start.sh"
    healthcheck = {
        "endpoint": "/admin",
        "port": "9002",
    }

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        checks = (context.charm._state.frontend_truststore_initialized is True,)
        return is_ready and all(checks)

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (
            context.charm._state.ran_upgrade,
            utils.get_from_optional_dict(context.charm._state.kafka_connection, "initialized"),
            utils.get_from_optional_dict(context.charm._state.opensearch_connection, "initialized"),
            GMSService.is_enabled(context),
        )
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:  # noqa C901
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.

        Raises:
            ImproperSecretError: If 'oidc-secret-id' points to a secret with bad contents.
        """
        if not cls.is_enabled(context):
            return None

        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection

        encryption_secret = context.charm.model.get_secret(id=context.charm.config.encryption_keys_secret_id)
        frontend_secret_key = encryption_secret.get_content(refresh=True)["frontend-key"]

        env = {
            "THEME_V2_DEFAULT": "true",
            # TODO (mertalpt): To be implemented with to o11y update.
            "ENABLE_PROMETHEUS": "false",
            # TODO (mertalpt): This changes when we split services into multiple charms.
            "DATAHUB_GMS_HOST": "localhost",
            "DATAHUB_GMS_PORT": "8080",
            "DATAHUB_SECRET": frontend_secret_key,
            "DATAHUB_APP_VERSION": "1.1.0",
            "DATAHUB_PLAY_MEM_BUFFER_SIZE": "10MB",
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "KAFKA_BOOTSTRAP_SERVER": kafka_conn["bootstrap_server"],
            "ENFORCE_VALID_EMAIL": "true",
            "KAFKA_PRODUCER_COMPRESSION_TYPE": "none",
            "KAFKA_PRODUCER_MAX_REQUEST_SIZE": "5242880",
            "KAFKA_CONSUMER_MAX_PARTITION_FETCH_BYTES": "5242880",
            "SPRING_KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "SPRING_KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "SPRING_KAFKA_PROPERTIES_SASL_JAAS_CONFIG": (
                "org.apache.kafka.common.security.scram.ScramLoginModule required "
                f'username="{kafka_conn["username"]}" password="{kafka_conn["password"]}";'
            ),
            "ELASTIC_CLIENT_HOST": os_conn["host"],
            "ELASTIC_CLIENT_PORT": os_conn["port"],
            "ELASTIC_CLIENT_USE_SSL": "true",
            "ELASTIC_CLIENT_USERNAME": os_conn["username"],
            "ELASTIC_CLIENT_PASSWORD": os_conn["password"],
            "AUTH_SESSION_TTL_HOURS": "24",
            # TODO (mertalpt): Consider making this a config option.
            # Required for access tokens, i.e. service accounts.
            "METADATA_SERVICE_AUTH_ENABLED": "true",
        }
        # Ref: https://datahubproject.io/docs/troubleshooting/quickstart/#ive-configured-oidc-but-i-cannot-login-i-get-continuously-redirected-what-do-i-do  # noqa
        if context.charm.config.use_play_cache_session_store:
            env["PAC4J_SESSIONSTORE_PROVIDER"] = "PlayCacheSessionStore"
        if context.charm.config.opensearch_index_prefix:
            env["ELASTIC_INDEX_PREFIX"] = context.charm.config.opensearch_index_prefix
        kafka_env = _kafka_topic_names(context.charm.config.kafka_topic_prefix)
        env.update(kafka_env)

        # Set up proxies if needed.
        # Ref: https://datahubproject.io/docs/authentication/guides/sso/configure-oidc-behind-proxy/  # noqa
        proxy_vars = {}
        no_proxy_hosts = ["localhost"]
        if os.getenv("JUJU_CHARM_NO_PROXY"):
            no_proxy_hosts.extend(str(os.getenv("JUJU_CHARM_NO_PROXY")).split(","))
        if env.get("DATAHUB_GMS_HOST"):
            no_proxy_hosts.extend(env["DATAHUB_GMS_HOST"])

        if http_proxy_raw := os.getenv("JUJU_CHARM_HTTP_PROXY"):
            http_proxy = urlparse(http_proxy_raw)
            proxy_vars["HTTP_PROXY_HOST"] = http_proxy.hostname
            proxy_vars["HTTP_PROXY_PORT"] = str(http_proxy.port or "")
        if https_proxy_raw := os.getenv("JUJU_CHARM_HTTPS_PROXY"):
            https_proxy = urlparse(https_proxy_raw)
            proxy_vars["HTTPS_PROXY_HOST"] = https_proxy.hostname
            proxy_vars["HTTPS_PROXY_PORT"] = str(https_proxy.port or "")

        proxy_vars["HTTP_NON_PROXY_HOSTS"] = "|".join(no_proxy_hosts)
        env.update(proxy_vars)

        # Set up OIDC if needed.
        if context.charm.config.oidc_secret_id is None:
            return env

        oidc_secret = context.charm.model.get_secret(id=context.charm.config.oidc_secret_id)
        content = oidc_secret.get_content(refresh=True)
        if "" in (
            content.get("client-id", ""),
            content.get("client-secret", ""),
        ):
            raise exceptions.ImproperSecretError("secret pointed to by 'oidc-secret-id' has improper contents")

        oidc_base_url = "http://localhost:9002"
        if context.charm.config.external_fe_hostname:
            # OIDC mandates the use of TLS, so the protocol is correct.
            # TODO (mertalpt): Add a check when OIDC is enabled without TLS.
            oidc_base_url = f"https://{context.charm.config.external_fe_hostname}"

        oidc_env = {
            "AUTH_OIDC_ENABLED": "true",
            "AUTH_OIDC_DISCOVERY_URI": "https://accounts.google.com/.well-known/openid-configuration",
            "AUTH_OIDC_BASE_URL": oidc_base_url,
            "AUTH_OIDC_SCOPE": "openid profile email",
            "AUTH_OIDC_CLIENT_ID": content["client-id"],
            "AUTH_OIDC_CLIENT_SECRET": content["client-secret"],
            "AUTH_OIDC_USER_NAME_CLAIM": "email",
        }
        env.update(oidc_env)

        return env

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        if not cls.is_ready(context):
            logger.info("datahub-frontend is not ready to be initialized, skipping initialization")
            return False

        # The only initialization step currently is to set up truststore for Opensearch SSL.
        check_frontend_truststore = context.charm._state.frontend_truststore_initialized is True
        if check_frontend_truststore:
            logger.debug("datahub-frontend is already initialized, skipping initialization")
            return False

        certificates = context.charm._state.opensearch_connection["tls-ca"]
        root_ca_cert = utils.split_certificates(certificates)[1]

        container = context.charm.unit.get_container(cls.name)

        try:
            utils.push_file(
                container,
                literals.TRUSTSTORE_INIT_SCRIPT_SRC_PATH,
                literals.TRUSTSTORE_INIT_SCRIPT_DEST_PATH,
                0o755,
            )
            utils.push_contents_to_file(container, root_ca_cert, literals.OPENSEARCH_ROOT_CA_CERT_PATH, 0o644)
            process = container.exec(
                [literals.TRUSTSTORE_INIT_SCRIPT_DEST_PATH],
                environment={
                    "CERT_PATH": literals.OPENSEARCH_ROOT_CA_CERT_PATH,
                    "CERT_ALIAS": literals.OPENSEARCH_ROOT_CA_CERT_ALIAS,
                },
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed to initialize truststore for datahub-frontend: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize truststores for datahub-frontend")

        logger.info("Successful truststore initialization for datahub-frontend")
        context.charm._state.frontend_truststore_initialized = True
        return True


class GMSService(AbstractService):
    """Service class for DataHub GMS.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-gms"
    command = "/bin/sh -c /datahub/datahub-gms/scripts/start.sh"
    healthcheck = {
        "endpoint": "/health",
        "port": "8080",
    }

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        checks = (context.charm._state.gms_truststore_initialized is True,)
        return is_ready and all(checks)

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (
            context.charm._state.ran_upgrade,
            utils.get_from_optional_dict(context.charm._state.database_connection, "initialized"),
            utils.get_from_optional_dict(context.charm._state.kafka_connection, "initialized"),
            utils.get_from_optional_dict(context.charm._state.opensearch_connection, "initialized"),
        )
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        if not cls.is_enabled(context):
            return None

        db_conn = context.charm._state.database_connection
        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection

        encryption_secret = context.charm.model.get_secret(id=context.charm.config.encryption_keys_secret_id)
        gms_secret_key = encryption_secret.get_content(refresh=True)["gms-key"]

        env = {
            "DATAHUB_TELEMETRY_ENABLED": "false",
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
            # TODO (mertalpt): If we ever implement external registry (e.g. Karapace),
            # then this needs to change.
            "KAFKA_SCHEMAREGISTRY_URL": "http://localhost:8080/schema-registry/api/",
            "SCHEMA_REGISTRY_TYPE": "INTERNAL",
            "SPRING_KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "SPRING_KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "SPRING_KAFKA_PROPERTIES_SASL_JAAS_CONFIG": (
                "org.apache.kafka.common.security.scram.ScramLoginModule required "
                f'username="{kafka_conn["username"]}" password="{kafka_conn["password"]}";'
            ),
            "ELASTICSEARCH_HOST": os_conn["host"],
            "ELASTICSEARCH_PORT": os_conn["port"],
            "SKIP_ELASTICSEARCH_CHECK": "true",
            "ELASTICSEARCH_USE_SSL": "true",
            "ELASTICSEARCH_USERNAME": os_conn["username"],
            "ELASTICSEARCH_PASSWORD": os_conn["password"],
            "GRAPH_SERVICE_IMPL": "elasticsearch",
            "UI_INGESTION_ENABLED": "true",
            "SECRET_SERVICE_ENCRYPTION_KEY": gms_secret_key,
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
            # TODO (mertalpt): Consider making this a config option.
            # Required for access tokens, i.e. service accounts.
            "METADATA_SERVICE_AUTH_ENABLED": "true",
        }
        if context.charm.config.opensearch_index_prefix:
            env["INDEX_PREFIX"] = context.charm.config.opensearch_index_prefix
        kafka_env = _kafka_topic_names(context.charm.config.kafka_topic_prefix)
        env.update(kafka_env)
        return env

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        if not cls.is_ready(context):
            logger.info("datahub-gms is not ready to be initialized, skipping initialization")
            return False

        # The only initialization step currently is to set up truststore for Opensearch SSL.
        check_gms_truststore = context.charm._state.gms_truststore_initialized is True
        if check_gms_truststore:
            logger.debug("datahub-gms is already initialized, skipping initialization")
            return False

        certificates = context.charm._state.opensearch_connection["tls-ca"]
        root_ca_cert = utils.split_certificates(certificates)[1]

        container = context.charm.unit.get_container(cls.name)

        try:
            utils.push_file(
                container,
                literals.TRUSTSTORE_INIT_SCRIPT_SRC_PATH,
                literals.TRUSTSTORE_INIT_SCRIPT_DEST_PATH,
                0o755,
            )
            utils.push_contents_to_file(container, root_ca_cert, literals.OPENSEARCH_ROOT_CA_CERT_PATH, 0o644)
            process = container.exec(
                [literals.TRUSTSTORE_INIT_SCRIPT_DEST_PATH],
                environment={
                    "CERT_PATH": literals.OPENSEARCH_ROOT_CA_CERT_PATH,
                    "CERT_ALIAS": literals.OPENSEARCH_ROOT_CA_CERT_ALIAS,
                },
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed truststore initialization for datahub-gms: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize truststores for datahub-gms")

        logger.info("Successful truststore initialization for datahub-gms")
        context.charm._state.gms_truststore_initialized = True
        return True


class OpensearchSetupService(AbstractService):
    """Service class for Opensearch setup job.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-opensearch-setup"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"
    healthcheck = None

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        return is_ready

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (utils.get_from_optional_dict(context.charm._state.opensearch_connection, "initialized") is not None,)
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        if not cls.is_enabled(context):
            return None

        conn = context.charm._state.opensearch_connection
        env = {
            "ELASTICSEARCH_HOST": conn["host"],
            "ELASTICSEARCH_PORT": conn["port"],
            "SKIP_ELASTICSEARCH_CHECK": "false",
            "ELASTICSEARCH_INSECURE": "false",
            "ELASTICSEARCH_USE_SSL": "true",
            "ELASTICSEARCH_USERNAME": conn["username"],
            "ELASTICSEARCH_PASSWORD": conn["password"],
            "INDEX_PREFIX": context.charm.config.opensearch_index_prefix,
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "USE_AWS_ELASTICSEARCH": "true",
        }
        if context.charm.config.opensearch_index_prefix:
            env["INDEX_PREFIX"] = context.charm.config.opensearch_index_prefix

        return env

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.

        Raises:
            InitializationFailedError: If the initialization fails.
            BadLogicError: If Opensearch is being initialized in an impossible state.
        """
        if not cls.is_ready(context):
            logger.info("Opensearch is not ready to be initialized, skipping initialization")
            return False

        is_initialized = utils.get_from_optional_dict(context.charm._state.opensearch_connection, "initialized")
        if is_initialized:
            logger.debug("Opensearch is already initialized, skipping initialization")
            return False

        logger.info("Running Opensearch initialization")
        container = context.charm.unit.get_container(cls.name)
        environment = cls.compile_environment(context)
        if environment is None:
            raise exceptions.BadLogicError("Opensearch is being initialized before it is ready!")

        # 'create-indices.sh' requires intervention to initialize Opensearch through SSL
        # That intervention can be in the form of setting an environment variable to let
        # curl use the required certificates.
        environment["CURL_CA_BUNDLE"] = literals.OPENSEARCH_CERTIFICATES_PATH
        certificates = context.charm._state.opensearch_connection["tls-ca"]
        logger.info("Pushing initialization script for Opensearch")
        utils.push_file(
            container,
            literals.RUNNER_SRC_PATH,
            literals.RUNNER_DEST_PATH,
            0o755,
        )
        utils.push_contents_to_file(container, certificates, literals.OPENSEARCH_CERTIFICATES_PATH, 0o644)
        try:
            logger.info("Running the initialization script for Opensearch")
            process = container.exec(
                command=[literals.RUNNER_DEST_PATH, "/create-indices.sh"],
                timeout=600,
                environment=environment,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed Opensearch initialization: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize opensearch")

        logger.info("Successful Opensearch initialization")
        conn = context.charm._state.opensearch_connection
        conn["initialized"] = True
        context.charm._state.opensearch_connection = conn
        return True


class KafkaSetupService(AbstractService):
    """Service class for Kafka setup job.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-kafka-setup"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"
    healthcheck = None

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        return is_ready

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (utils.get_from_optional_dict(context.charm._state.kafka_connection, "initialized") is not None,)
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        if not cls.is_enabled(context):
            return None

        conn = context.charm._state.kafka_connection
        environment = {
            "KAFKA_BOOTSTRAP_SERVER": conn["bootstrap_server"],
            # The value for this is not actually used in the container.
            "KAFKA_ZOOKEEPER_CONNECT": "",
            "MAX_MESSAGE_BYTES": "5242880",
            "USE_CONFLUENT_SCHEMA_REGISTRY": "false",
            # TODO (mertalpt): Figure out how to switch these based on Kafka's SSL status.
            "KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "KAFKA_PROPERTIES_SASL_JAAS_CONFIG": (
                "org.apache.kafka.common.security.scram.ScramLoginModule required "
                f'username="{conn["username"]}" password="{conn["password"]}";'
            ),
        }
        topic_names = _kafka_topic_names(context.charm.config.kafka_topic_prefix)
        environment.update(topic_names)

        return environment

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        if not cls.is_ready(context):
            logger.info("Kafka is not ready to be initialized, skipping initialization")
            return False

        is_initialized = utils.get_from_optional_dict(context.charm._state.kafka_connection, "initialized")
        if is_initialized:
            logger.debug("Kafka is already initialized, skipping initialization")
            return False

        logger.info("Running Kafka initialization")
        container = context.charm.unit.get_container(cls.name)
        environment = cls.compile_environment(context)
        logger.info("Pushing initialization script for Kafka")
        utils.push_file(
            container,
            literals.RUNNER_SRC_PATH,
            literals.RUNNER_DEST_PATH,
            0o744,
        )
        try:
            logger.info("Running the initialization script for Kafka")
            process = container.exec(
                command=[literals.RUNNER_DEST_PATH, "/opt/kafka/kafka-setup.sh"],
                working_dir="/opt/kafka",
                timeout=600,
                environment=environment,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed Kafka initialization: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize kafka")

        logger.info("Successful Kafka initialization")
        conn = context.charm._state.kafka_connection
        conn["initialized"] = True
        context.charm._state.kafka_connection = conn
        return True


class PostgresqlSetupService(AbstractService):
    """Service class for Postgresql setup job.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-postgresql-setup"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"
    healthcheck = None

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        return is_ready

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (utils.get_from_optional_dict(context.charm._state.database_connection, "initialized") is not None,)
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        if not cls.is_enabled(context):
            return None

        conn = context.charm._state.database_connection
        environment = {
            "POSTGRES_USERNAME": conn["username"],
            "POSTGRES_PASSWORD": conn["password"],
            "POSTGRES_HOST": conn["host"],
            "POSTGRES_PORT": conn["port"],
            "DATAHUB_DB_NAME": conn["dbname"],
        }

        return environment

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        if not cls.is_ready(context):
            logger.info("db is not ready to be initialized, skipping initialization")
            return False

        is_initialized = utils.get_from_optional_dict(context.charm._state.database_connection, "initialized")
        if is_initialized:
            logger.debug("db is already initialized, skipping initialization")
            return False

        logger.info("Running db initialization")
        container = context.charm.unit.get_container(cls.name)
        environment = cls.compile_environment(context)
        logger.info("Pushing initialization script for db")
        utils.push_file(
            container,
            literals.RUNNER_SRC_PATH,
            literals.RUNNER_DEST_PATH,
            0o744,
        )
        try:
            logger.info("Running the initialization script for db")
            process = container.exec(
                command=[literals.RUNNER_DEST_PATH, "/init.sh"],
                timeout=600,
                environment=environment,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed db initialization: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize db")

        logger.info("Successful db initialization")
        conn = context.charm._state.database_connection
        conn["initialized"] = True
        context.charm._state.database_connection = conn
        return True


class UpgradeService(AbstractService):
    """Service class for DataHub upgrade job.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-upgrade"
    # Idle workload, actions will be run on a trigger basis.
    command = "/usr/bin/tail -f /dev/null"
    healthcheck = None

    @classmethod
    def is_enabled(cls, context: ServiceContext) -> bool:
        """Determine if the service should be enabled using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service should be enabled.
        """
        is_ready = cls.is_ready(context)
        return is_ready

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (
            utils.get_from_optional_dict(context.charm._state.database_connection, "initialized") is not None,
            utils.get_from_optional_dict(context.charm._state.kafka_connection, "initialized") is not None,
            utils.get_from_optional_dict(context.charm._state.opensearch_connection, "initialized") is not None,
        )
        return all(checks)

    @classmethod
    def compile_environment(cls, context: ServiceContext) -> Optional[Dict[str, str]]:
        """Compile service specific environment variables from the context.

        Args:
            context: Context for the service.

        Returns:
            If ready, a dictionary of environment variables for the service.
        """
        if not cls.is_enabled(context):
            return None

        db_conn = context.charm._state.database_connection
        kafka_conn = context.charm._state.kafka_connection
        os_conn = context.charm._state.opensearch_connection

        env = {
            # From: https://github.com/acryldata/datahub-helm/blob/master/charts/datahub/templates/datahub-upgrade/datahub-system-update-job.yml  # noqa: W505
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "SCHEMA_REGISTRY_SYSTEM_UPDATE": "true",
            "SPRING_KAFKA_PROPERTIES_AUTO_REGISTER_SCHEMAS": "true",
            "SPRING_KAFKA_PROPERTIES_USE_LATEST_VERSION": "true",
            "SCHEMA_REGISTRY_TYPE": "INTERNAL",
            "ELASTICSEARCH_BUILD_INDICES_CLONE_INDICES": "false",
            "ELASTICSEARCH_INDEX_BUILDER_MAPPINGS_REINDEX": "true",
            "ELASTICSEARCH_INDEX_BUILDER_SETTINGS_REINDEX": "true",
            "ELASTICSEARCH_BUILD_INDICES_ALLOW_DOC_COUNT_MISMATCH": "false",
            # From: https://github.com/acryldata/datahub-helm/blob/master/charts/datahub/templates/datahub-upgrade/_upgrade.tpl  # noqa: W505
            "ENTITY_REGISTRY_CONFIG_PATH": "/datahub/datahub-gms/resources/entity-registry.yml",
            "DATAHUB_GMS_HOST": "localhost",
            "DATAHUB_GMS_PORT": "8080",
            "EBEAN_DATASOURCE_USERNAME": db_conn["username"],
            "EBEAN_DATASOURCE_PASSWORD": db_conn["password"],
            "EBEAN_DATASOURCE_HOST": f"{db_conn['host']}:{db_conn['port']}",
            "EBEAN_DATASOURCE_URL": f"jdbc:postgresql://{db_conn['host']}:{db_conn['port']}/{db_conn['dbname']}",
            "EBEAN_DATASOURCE_DRIVER": "org.postgresql.Driver",
            "KAFKA_BOOTSTRAP_SERVER": kafka_conn["bootstrap_server"],
            "KAFKA_PRODUCER_COMPRESSION_TYPE": "none",
            "KAFKA_PRODUCER_MAX_REQUEST_SIZE": "5242880",
            "KAFKA_CONSUMER_MAX_PARTITION_FETCH_BYTES": "5242880",
            # TODO (mertalpt): If we ever implement external registry (e.g. Karapace),
            # then this needs to change.
            "KAFKA_SCHEMAREGISTRY_URL": "http://localhost:8080/schema-registry/api/",
            "ELASTICSEARCH_HOST": os_conn["host"],
            "ELASTICSEARCH_PORT": os_conn["port"],
            "SKIP_ELASTICSEARCH_CHECK": "true",
            "ELASTICSEARCH_INSECURE": "false",
            "ELASTICSEARCH_USE_SSL": "true",
            "ELASTICSEARCH_USERNAME": os_conn["username"],
            "ELASTICSEARCH_PASSWORD": os_conn["password"],
            "GRAPH_SERVICE_IMPL": "elasticsearch",
            "SPRING_KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "SPRING_KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "SPRING_KAFKA_PROPERTIES_SASL_JAAS_CONFIG": (
                "org.apache.kafka.common.security.scram.ScramLoginModule required "
                f'username="{kafka_conn["username"]}" password="{kafka_conn["password"]}";'
            ),
        }
        if context.charm.config.opensearch_index_prefix:
            env["INDEX_PREFIX"] = context.charm.config.opensearch_index_prefix
        kafka_topics = _kafka_topic_names(context.charm.config.kafka_topic_prefix)
        env.update(kafka_topics)

        return env

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run service specific initialization scripts if service is ready and it is necessary.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        # In order to fit the pattern of services, we loosen the semantics.
        # The "initialization" for 'Upgrade' actually runs an upgrade for the whole ecosystem.
        if context.charm._state.ran_upgrade:
            logger.debug("Already ran datahub-upgrade, skipping initialization")
            return False

        # We need to ensure that the truststore is configured first.
        check_upgrade_truststore = context.charm._state.upgrade_truststore_initialized
        if check_upgrade_truststore:
            logger.debug("datahub-upgrade truststore is already initialized")
            return False

        check_opensearch = utils.get_from_optional_dict(context.charm._state.opensearch_connection, "initialized")
        if not check_opensearch:
            logger.info("Opensearch is not initialized yet, skipping running datahub-upgrade")
            return False

        certificates = context.charm._state.opensearch_connection["tls-ca"]
        root_ca_cert = utils.split_certificates(certificates)[1]

        container = context.charm.unit.get_container(cls.name)

        try:
            utils.push_file(
                container,
                literals.TRUSTSTORE_INIT_SCRIPT_SRC_PATH,
                literals.TRUSTSTORE_INIT_SCRIPT_DEST_PATH,
                0o755,
            )
            utils.push_contents_to_file(container, root_ca_cert, literals.OPENSEARCH_ROOT_CA_CERT_PATH, 0o644)
            process = container.exec(
                [literals.TRUSTSTORE_INIT_SCRIPT_DEST_PATH],
                environment={
                    "CERT_PATH": literals.OPENSEARCH_ROOT_CA_CERT_PATH,
                    "CERT_ALIAS": literals.OPENSEARCH_ROOT_CA_CERT_ALIAS,
                },
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed truststore initialization for datahub-upgrade: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize truststores for datahub-upgrade")

        logger.info("Successful truststore initialization for datahub-upgrade")
        context.charm._state.frontend_truststore_initialized = True

        logger.info("Pushing runner script for datahub-upgrade")
        utils.push_file(
            container,
            literals.RUNNER_SRC_PATH,
            literals.RUNNER_DEST_PATH,
            0o755,
        )

        # We run the upgrade job now.
        logger.info("Running datahub-upgrade job")
        environment = cls.compile_environment(context)
        try:
            process = container.exec(
                [
                    literals.RUNNER_DEST_PATH,
                    "java",
                    "-jar",
                    "/datahub/datahub-upgrade/bin/datahub-upgrade.jar",
                    "-u",
                    "SystemUpdate",
                ],
                encoding="utf-8",
                environment=environment,
                timeout=180,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed job run for datahub-upgrade: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to run jobs for datahub-upgrade")

        logger.info("Successful datahub-upgrade run")
        context.charm._state.ran_upgrade = True
        return True
