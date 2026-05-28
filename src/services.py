# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
# pylint: disable=C0302

"""Define DataHub services."""

# TODO (mertalpt): Convert the module to a package and split up services.

# Some General Notes
# 1. `process.wait()` causes some scripts (i.e. datahub-upgrade) to get stuck, which is why we use
#    `process.wait_output()` even though we do not read the output. It does not have the same
#    problem for some reason.

import abc
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Optional
from urllib.parse import urlparse

import yaml
from ops.pebble import CheckStatus

import exceptions
import literals
import utils

if TYPE_CHECKING:
    from charm import DatahubK8SOperatorCharm  # noqa

logger = logging.getLogger(__name__)


def _import_certificates_to_truststore(container, certificates: str, alias_prefix: str) -> None:
    """Import all certificates in a PEM bundle into JVM cacerts.

    Args:
        container: Container where keytool is executed.
        certificates: PEM bundle (possibly containing multiple certificates).
        alias_prefix: Prefix for aliases used in keytool.
    """
    cert_chain = utils.split_certificates(certificates)
    for index, cert in enumerate(cert_chain):
        cert_path = f"/charm-external/opensearch_ca_{index}.pem"
        cert_alias = f"{alias_prefix}-{index}"

        utils.push_contents_to_file(container, cert, cert_path, 0o644)

        # Delete before import so a rotated CA is always refreshed.
        try:
            delete_process = container.exec(
                [
                    literals.KEYTOOL_BIN_PATH,
                    "-delete",
                    "-cacerts",
                    "-alias",
                    cert_alias,
                    "-storepass",
                    "changeit",
                ]
            )
            delete_process.wait_output()
        except Exception:
            logger.debug("Certificate alias '%s' not in truststore, nothing to delete", cert_alias)

        import_process = container.exec(
            [
                literals.KEYTOOL_BIN_PATH,
                "-importcert",
                "-cacerts",
                "-file",
                cert_path,
                "-alias",
                cert_alias,
                "-storepass",
                "changeit",
                "-noprompt",
            ],
        )
        import_process.wait_output()


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


def _compile_standard_proxy_environment(extra_no_proxy_hosts: Optional[List[str]] = None) -> Dict[str, str]:
    """Compile standard proxy variables from Juju charm proxy environment variables.

    Args:
        extra_no_proxy_hosts: Optional hostnames to append to NO_PROXY/no_proxy.

    Returns:
        Standard proxy variables suitable for Python tools.
    """
    proxy_vars: Dict[str, str] = {}
    proxy_map = {
        "JUJU_CHARM_HTTP_PROXY": ("HTTP_PROXY", "http_proxy"),
        "JUJU_CHARM_HTTPS_PROXY": ("HTTPS_PROXY", "https_proxy"),
    }

    has_proxy_config = False
    for juju_var, target_vars in proxy_map.items():
        value = os.getenv(juju_var)
        if not value:
            continue
        has_proxy_config = True
        for target in target_vars:
            proxy_vars[target] = value

    juju_no_proxy = os.getenv("JUJU_CHARM_NO_PROXY")
    if juju_no_proxy:
        has_proxy_config = True

    if has_proxy_config:
        no_proxy_values: List[str] = []
        if juju_no_proxy:
            no_proxy_values.extend(host.strip() for host in juju_no_proxy.split(",") if host.strip())
        if extra_no_proxy_hosts:
            no_proxy_values.extend(host.strip() for host in extra_no_proxy_hosts if host and host.strip())

        if no_proxy_values:
            unique_no_proxy = list(dict.fromkeys(no_proxy_values))
            no_proxy = ",".join(unique_no_proxy)
            proxy_vars["NO_PROXY"] = no_proxy
            proxy_vars["no_proxy"] = no_proxy

    return proxy_vars


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
    command = "/start_datahub_actions.sh"
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
            GMSService.is_enabled(context),
            context.charm.kafka_relation.connection is not None,
            context.charm.system_client_id,
            context.charm.system_client_secret,
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

        kafka_conn = context.charm.kafka_relation.connection

        env = {
            "JAVA_HOME": literals.JAVA_HOME,
            "VIRTUAL_ENV": "/opt/datahub/venv",
            "PATH": "/opt/datahub/venv/bin:/usr/bin:/bin",
            "HOME": "/home/datahub",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
            "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
            "DATAHUB_ACTIONS_SYSTEM_CONFIGS_PATH": "/etc/datahub/actions/system/conf",
            "DATAHUB_BUNDLED_VENV_PATH": "/opt/datahub/venvs",
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
        env.update(_compile_standard_proxy_environment(extra_no_proxy_hosts=["localhost", env["DATAHUB_GMS_HOST"]]))

        env["DATAHUB_SYSTEM_CLIENT_ID"] = context.charm.system_client_id
        env["DATAHUB_SYSTEM_CLIENT_SECRET"] = context.charm.system_client_secret

        return env


class FrontendService(AbstractService):
    """Service class for DataHub Frontend.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-frontend"
    command = "/start.sh"
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
        return cls.is_ready(context)

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        return GMSService.is_enabled(context)

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

        kafka_conn = context.charm.kafka_relation.connection
        os_conn = context.charm.opensearch_relation.connection

        encryption_secret = context.charm.model.get_secret(id=context.charm.config.encryption_keys_secret_id)
        frontend_secret_key = encryption_secret.get_content(refresh=True)["frontend-key"]

        env = {
            "JAVA_HOME": literals.JAVA_HOME,
            "THEME_V2_DEFAULT": "true",
            "MFE_CONFIG_FILE_PATH": "/datahub-frontend/conf/mfe.config.dev.yaml",
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
            "KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "KAFKA_PROPERTIES_SASL_JAAS_CONFIG": (
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
            no_proxy_hosts.append(env["DATAHUB_GMS_HOST"])

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

        env["DATAHUB_SYSTEM_CLIENT_ID"] = context.charm.system_client_id
        env["DATAHUB_SYSTEM_CLIENT_SECRET"] = context.charm.system_client_secret

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

        fe_ingress = context.charm.frontend_ingress
        oidc_base_url = fe_ingress.url if fe_ingress.is_ready() else literals.FRONTEND_FALLBACK_URL

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

        Initialization runs in order:
        1. Push user.props credential file
        2. Truststore initialization for Opensearch SSL

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

        container = context.charm.unit.get_container(cls.name)

        # Step 1: Push user.props credential file. If content is the same on
        # each reconcile, re-pushing is a no-op for the workload.
        password = context.charm._ensure_password()
        if not password:
            logger.info("Admin password not yet available, skipping frontend initialization")
            return False
        utils.push_contents_to_file(container, f"datahub:{password}", "/datahub-frontend/conf/user.props", 0o644)

        # Step 2: Truststore initialization for Opensearch SSL.
        # Runs unconditionally on every reconcile. _import_certificates_to_truststore
        # has per-alias dedup so re-runs are cheap; running every time is what
        # makes the truststore self-heal after missing cacerts.
        certificates = context.charm.opensearch_relation.connection["tls-ca"]
        try:
            _import_certificates_to_truststore(
                container,
                certificates,
                literals.OPENSEARCH_ROOT_CA_CERT_ALIAS,
            )
        except Exception as e:
            logger.info("Failed to initialize truststore for datahub-frontend: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize truststores for datahub-frontend")
        logger.debug("Truststore for datahub-frontend is up to date")

        # Step 3: Patch application.conf to serve static assets with immutable cache headers.
        # Play defaults to max-age=3600, which forces browsers to re-download and cold-parse
        # the 18MB JS bundle every hour. Assets use content-addressed filenames (hash in name),
        # so they are safe to cache indefinitely since the hash changes on each upgrade.
        app_conf_path = "/datahub-frontend/conf/application.conf"
        cache_directive = 'play.assets.cache."/public" = "public, max-age=31536000, immutable"'
        try:
            app_conf = container.pull(app_conf_path).read()
            if cache_directive not in app_conf:
                utils.push_contents_to_file(container, app_conf + f"\n{cache_directive}\n", app_conf_path, 0o644)
                logger.debug("Patched application.conf with immutable asset cache headers")
            else:
                logger.debug("application.conf already contains immutable asset cache directive, skipping")
        except Exception as e:
            logger.warning("Failed to patch application.conf for asset cache headers: '%s'", str(e))

        return True


class GMSService(AbstractService):
    """Service class for DataHub GMS.

    Attributes:
        name: Name of the service.
        command: Command to be executed to run the workload.
        healthcheck: Optional dictionary for healthcheck configuration.
    """

    name = "datahub-gms"
    command = "/datahub/datahub-gms/scripts/start.sh"
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
        return cls.is_ready(context)

    @classmethod
    def is_ready(cls, context: ServiceContext) -> bool:
        """Determine if the service is ready to be initialized using configs and context clues.

        Args:
            context: Context for the service.

        Returns:
            Whether the service is ready to be initialized.
        """
        checks = (
            context.charm.db_relation.connection is not None,
            context.charm.kafka_relation.connection is not None,
            context.charm.opensearch_relation.connection is not None,
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

        db_conn = context.charm.db_relation.connection
        kafka_conn = context.charm.kafka_relation.connection
        os_conn = context.charm.opensearch_relation.connection

        encryption_secret = context.charm.model.get_secret(id=context.charm.config.encryption_keys_secret_id)
        gms_secret_key = encryption_secret.get_content(refresh=True)["gms-key"]

        env = {
            "JAVA_HOME": literals.JAVA_HOME,
            "THEME_V2_DEFAULT": "true",
            "DATAHUB_TELEMETRY_ENABLED": "false",
            "EBEAN_DATASOURCE_PORT": db_conn["port"],
            "SHOW_SEARCH_FILTERS_V2": "true",
            "SHOW_BROWSE_V2": "true",
            "BACKFILL_BROWSE_PATHS_V2": "true",
            # TODO (mertalpt): To be implemented with to o11y update.
            "ENABLE_PROMETHEUS": "false",
            # MCE consumer is deprecated since DataHub 0.9.x+.
            # All ingestion uses the MCP/MCL path (rest sink), so it is not needed.
            "MCE_CONSUMER_ENABLED": "false",
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

        env["DATAHUB_SYSTEM_CLIENT_ID"] = context.charm.system_client_id
        env["DATAHUB_SYSTEM_CLIENT_SECRET"] = context.charm.system_client_secret

        return env

    @classmethod
    def _set_workload_version(cls, context: ServiceContext) -> None:
        """Extract and set the workload version from the GMS container's rockcraft.yaml.

        `unit.set_workload_version` is a no-op when the value is unchanged,
        so it's safe to run on every reconcile rather than gate on peer state.

        Args:
            context: Context for the service.
        """
        container = context.charm.unit.get_container(cls.name)
        try:
            meta_file = container.pull("/rockcraft.yaml")
            meta = yaml.safe_load(meta_file)
            if not meta or "version" not in meta:
                raise ValueError("Cannot find 'version' in 'rockcraft.yaml'.")
            context.charm.unit.set_workload_version(meta["version"])
        except Exception as e:
            logger.warning("Could not set workload version: %s", str(e))

    @classmethod
    def run_initialization(cls, context: ServiceContext) -> bool:
        """Run all initialization steps for the GMS container.

        The GMS rock bundles all setup scripts (PostgreSQL, Opensearch, upgrade)
        in addition to the GMS service itself. Initialization runs in order:
        0. Workload version extraction (safe, no relation dependencies)
        1. PostgreSQL setup (init.sh)
        2. Opensearch index creation (create-indices.sh)
        3. Truststore initialization for Opensearch SSL
        4. DataHub upgrade (SystemUpdate job, also handles Kafka topic creation)

        Statelessness note: steps 1, 2 and 4 are one-time backend bootstrap. They
        only need to run while GMS is not yet serving. Once the `up` health check
        passes, the schema, indices and SystemUpdate marker all exist, so we skip
        them to avoid the JVM-heavy upgrade on every reconcile. They re-run
        automatically on a fresh container (no pebble plan yet -> `up` check absent).
        The truststore import (step 3) is exempt: it is cheap and must self-heal
        unconditionally.

        Args:
            context: Context for the service.

        Returns:
            If initialization scripts were run and were successful.
        """
        # Workload version is a safe read-only operation that doesn't require relations.
        cls._set_workload_version(context)

        if not cls.is_ready(context):
            logger.info("datahub-gms is not ready to be initialized, skipping initialization")
            return False

        container = context.charm.unit.get_container(cls.name)
        gms_serving = cls._gms_is_serving(container)

        if gms_serving:
            logger.debug("GMS is already serving; skipping one-time backend bootstrap")
        else:
            # Step 1: PostgreSQL setup
            cls._run_postgresql_setup(context, container)
            # Step 2: Opensearch index creation
            cls._run_opensearch_setup(context, container)

        # Step 3: Truststore initialization
        cls._run_truststore_init(context, container)

        if not gms_serving:
            # Step 4: DataHub upgrade (SystemUpdate)
            cls._run_upgrade(context, container)

        return True

    @staticmethod
    def _gms_is_serving(container) -> bool:
        """Return True when the GMS `up` health check is passing.

        Used as a stateless precondition: a serving GMS implies the one-time
        backend bootstrap (schema, indices, SystemUpdate) has already completed.
        Returns False on a fresh container where the pebble plan, and therefore
        the `up` check, does not exist yet.

        Args:
            container: The GMS container reference.

        Returns:
            Whether the GMS `up` check reports UP.
        """
        try:
            check = container.get_check("up")
        except Exception:  # noqa: BLE001 — check absent on fresh container
            return False
        return check.status == CheckStatus.UP

    @classmethod
    def _run_postgresql_setup(cls, context: ServiceContext, container) -> None:
        """Run PostgreSQL setup against the related DB.

        Idempotent since the init script uses `CREATE TABLE IF NOT EXISTS` etc.
        Gated by the caller on GMS-not-serving, so it runs on fresh containers
        and self-heals on pod recreation rather than on every reconcile.

        Args:
            context: Context for the service.
            container: The GMS container reference.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        db_conn = context.charm.db_relation.connection
        environment = {
            "POSTGRES_USERNAME": db_conn["username"],
            "POSTGRES_PASSWORD": db_conn["password"],
            "POSTGRES_HOST": db_conn["host"],
            "POSTGRES_PORT": db_conn["port"],
            "DATAHUB_DB_NAME": db_conn["dbname"],
        }

        try:
            logger.info("Running the initialization script for db")
            process = container.exec(
                command=[literals.RUNNER_PATH, literals.POSTGRES_SETUP_SCRIPT],
                working_dir=literals.POSTGRES_SETUP_WORKDIR,
                timeout=600,
                environment=environment,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed db initialization: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize db")

        logger.info("Successful db initialization")

    @classmethod
    def _run_opensearch_setup(cls, context: ServiceContext, container) -> None:
        """Run Opensearch index creation against the related cluster.

        Idempotent since `create-indices.sh` uses `PUT` with `IF NOT EXISTS`
        semantics. Gated by the caller on GMS-not-serving, so it runs on fresh
        containers and self-heals on pod recreation rather than every reconcile.

        Args:
            context: Context for the service.
            container: The GMS container reference.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        os_conn = context.charm.opensearch_relation.connection
        environment = {
            "ELASTICSEARCH_HOST": os_conn["host"],
            "ELASTICSEARCH_PORT": os_conn["port"],
            "SKIP_ELASTICSEARCH_CHECK": "false",
            "ELASTICSEARCH_INSECURE": "false",
            "ELASTICSEARCH_USE_SSL": "true",
            "ELASTICSEARCH_USERNAME": os_conn["username"],
            "ELASTICSEARCH_PASSWORD": os_conn["password"],
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "USE_AWS_ELASTICSEARCH": "true",
        }
        if context.charm.config.opensearch_index_prefix:
            environment["INDEX_PREFIX"] = context.charm.config.opensearch_index_prefix

        # 'create-indices.sh' requires intervention to initialize Opensearch through SSL
        # That intervention can be in the form of setting an environment variable to let
        # curl use the required certificates.
        environment["CURL_CA_BUNDLE"] = literals.OPENSEARCH_CERTIFICATES_PATH
        utils.push_contents_to_file(container, os_conn["tls-ca"], literals.OPENSEARCH_CERTIFICATES_PATH, 0o644)
        try:
            logger.info("Running the initialization script for Opensearch")
            process = container.exec(
                command=[literals.RUNNER_PATH, literals.OPENSEARCH_SETUP_SCRIPT],
                working_dir=literals.OPENSEARCH_SETUP_WORKDIR,
                timeout=600,
                environment=environment,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed Opensearch initialization: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize opensearch")

        logger.info("Successful Opensearch initialization")

    @classmethod
    def _run_truststore_init(cls, context: ServiceContext, container) -> None:
        """Initialize the Java truststore for Opensearch SSL.

        Args:
            context: Context for the service.
            container: The GMS container reference.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        certificates = context.charm.opensearch_relation.connection["tls-ca"]

        try:
            _import_certificates_to_truststore(
                container,
                certificates,
                literals.OPENSEARCH_ROOT_CA_CERT_ALIAS,
            )
        except Exception as e:
            logger.info("Failed truststore initialization for datahub-gms: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to initialize truststores for datahub-gms")

        logger.debug("Truststore for datahub-gms is up to date")

    @classmethod
    def _run_upgrade(cls, context: ServiceContext, container) -> None:
        """Run the DataHub SystemUpdate upgrade job.

        This also handles Kafka topic creation (merged upstream).

        Args:
            context: Context for the service.
            container: The GMS container reference.

        Raises:
            InitializationFailedError: If the initialization fails.
        """
        # SystemUpdate is the JVM-heavy bootstrap. The caller gates it on
        # GMS-not-serving so it runs once per container lifecycle (fresh pod)
        # rather than on every reconcile. SystemUpdate is itself idempotent, so
        # re-running on pod recreation is safe.
        logger.info("Running datahub-upgrade job")
        environment = cls._compile_upgrade_environment(context)
        try:
            process = container.exec(
                [
                    literals.RUNNER_PATH,
                    literals.JAVA_BIN_PATH,
                    "-jar",
                    literals.UPGRADE_JAR_PATH,
                    "-u",
                    "SystemUpdate",
                ],
                encoding="utf-8",
                environment=environment,
                timeout=600,
            )
            process.wait_output()
        except Exception as e:
            logger.info("Failed job run for datahub-upgrade: '%s'", str(e))
            raise exceptions.InitializationFailedError("failed to run jobs for datahub-upgrade")

        logger.info("Successful datahub-upgrade run")

    @classmethod
    def _compile_upgrade_environment(cls, context: ServiceContext) -> Dict[str, str]:
        """Compile environment variables for the upgrade job.

        Args:
            context: Context for the service.

        Returns:
            A dictionary of environment variables for the upgrade job.
        """
        db_conn = context.charm.db_relation.connection
        kafka_conn = context.charm.kafka_relation.connection
        os_conn = context.charm.opensearch_relation.connection

        env = {
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "JAVA_TOOL_OPTIONS": (
                f"-Djavax.net.ssl.trustStore={literals.JAVA_HOME}/lib/security/cacerts "
                "-Djavax.net.ssl.trustStorePassword=changeit"
            ),
            "SCHEMA_REGISTRY_SYSTEM_UPDATE": "true",
            "SPRING_KAFKA_PROPERTIES_AUTO_REGISTER_SCHEMAS": "true",
            "SPRING_KAFKA_PROPERTIES_USE_LATEST_VERSION": "true",
            "SCHEMA_REGISTRY_TYPE": "INTERNAL",
            "ELASTICSEARCH_BUILD_INDICES_CLONE_INDICES": "false",
            "ELASTICSEARCH_INDEX_BUILDER_MAPPINGS_REINDEX": "true",
            "ELASTICSEARCH_INDEX_BUILDER_SETTINGS_REINDEX": "true",
            "ELASTICSEARCH_BUILD_INDICES_ALLOW_DOC_COUNT_MISMATCH": "false",
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
            "KAFKA_SCHEMAREGISTRY_URL": "http://localhost:8080/schema-registry/api/",
            "ELASTICSEARCH_HOST": os_conn["host"],
            "ELASTICSEARCH_PORT": os_conn["port"],
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
    def run_reindex(cls, context: ServiceContext, clean: bool) -> bool:
        """Run reindexing using the GMS container.

        Args:
            context: Context for the service.
            clean: Whether or not to run a clean reindex.

        Returns:
            If reindexing was run and was successful.
        """
        container = context.charm.unit.get_container(cls.name)

        logger.info("Running reindexing using datahub-upgrade")
        environment = cls._compile_upgrade_environment(context)
        command = [
            literals.RUNNER_PATH,
            literals.JAVA_BIN_PATH,
            "-jar",
            literals.UPGRADE_JAR_PATH,
            "-u",
            "RestoreIndices",
        ]

        if clean:
            command.extend(["-a", "clean"])

        try:
            container.exec(
                command,
                encoding="utf-8",
                environment=environment,
                timeout=180,
            )
            logger.info("Reindex process started asynchronously.")
            return True
        except Exception as e:
            logger.error("Failed reindexing run for datahub-upgrade: %s", str(e))
            return False
