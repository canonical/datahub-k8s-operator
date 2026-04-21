# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals."""

DB_NAME = "datahub_db"
PLACEHOLDER_INDEX = "datahub_index"
PLACEHOLDER_TOPIC = "datahub_topic"
FRONTEND_PORT = 9002
GMS_PORT = 8080

INIT_PWD_SECRET_LABEL = "datahub-init-pwd"  # nosec B105
ENCRYPTION_KEYS_SECRET_LABEL = "datahub-encryption-keys"  # nosec B105
SYSTEM_CLIENT_SECRET_LABEL = "datahub-system-client-secret"  # nosec B105
INGESTION_TOKEN_SECRET_LABEL = "datahub-ingestion-token"  # nosec B105

# Paths for scripts baked into the rocks (see datahub_rocks/shared/scripts/).
RUNNER_PATH = "/charm-scripts/runner.sh"

OPENSEARCH_CERTIFICATES_PATH = "/charm-external/opensearch_certificates.pem"
OPENSEARCH_ROOT_CA_CERT_PATH = "/charm-external/opensearch_root_ca_cert.pem"
OPENSEARCH_ROOT_CA_CERT_ALIAS = "opensearch-root-ca"

# Paths inside the GMS rock for setup scripts.
POSTGRES_SETUP_SCRIPT = "/datahub/postgres-setup/init.sh"
POSTGRES_SETUP_WORKDIR = "/datahub/postgres-setup"
OPENSEARCH_SETUP_SCRIPT = "/datahub/elasticsearch-setup/create-indices.sh"
OPENSEARCH_SETUP_WORKDIR = "/datahub/elasticsearch-setup"
UPGRADE_JAR_PATH = "/datahub/datahub-upgrade/bin/datahub-upgrade.jar"
JAVA_HOME = "/usr/lib/jvm/java-17-openjdk-amd64"
JAVA_BIN_PATH = f"{JAVA_HOME}/bin/java"
KEYTOOL_BIN_PATH = f"{JAVA_HOME}/bin/keytool"
