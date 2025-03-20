# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals."""

DB_NAME = "datahub_db"
PLACEHOLDER_INDEX = "datahub_index"
PLACEHOLDER_TOPIC = "datahub_topic"
FRONTEND_PORT = 9002
GMS_PORT = 8080

INIT_PWD_SECRET_LABEL = "datahub-init-pwd"

RUNNER_SRC_PATH = ("src", "scripts", "runner.sh")
RUNNER_DEST_PATH = "/charm-external/runner.sh"
TRUSTSTORE_INIT_SCRIPT_SRC_PATH = ("src", "scripts", "init-truststore.sh")
TRUSTSTORE_INIT_SCRIPT_DEST_PATH = "/charm-external/init-truststore.sh"
OPENSEARCH_CERTIFICATES_PATH = "/charm-external/opensearch_certificates.pem"
OPENSEARCH_ROOT_CA_CERT_PATH = "/charm-external/opensearch_root_ca_cert.pem"
OPENSEARCH_ROOT_CA_CERT_ALIAS = "opensearch-root-ca"
