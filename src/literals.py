# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals."""

DB_NAME = "datahub_db"
PLACEHOLDER_INDEX = "datahub_index"
PLACEHOLDER_TOPIC = "datahub_topic"
FRONTEND_PORT = 9002

RUNNER_SRC_PATH = ("src", "scripts", "runner.sh")
RUNNER_DEST_PATH = "/tmp/charm/runner.sh"
TRUSTSTORE_INIT_SCRIPT_SRC_PATH = ("src", "scripts", "init-truststore.sh")
TRUSTSTORE_INIT_SCRIPT_DEST_PATH = "/tmp/charm/init-truststore.sh"
OPENSEARCH_CERTIFICATES_PATH = "/tmp/charm/opensearch_certificates.pem"
OPENSEARCH_ROOT_CA_CERT_PATH = "/tmp/charm/opensearch_root_ca_cert.pem"
OPENSEARCH_ROOT_CA_CERT_ALIAS = "opensearch-root-ca"
