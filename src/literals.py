# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals."""

DB_NAME = "datahub_db"
PLACEHOLDER_INDEX = "datahub_index"
PLACEHOLDER_TOPIC = "datahub_topic"
FRONTEND_PORT = 9002
FRONTEND_FALLBACK_URL = f"http://localhost:{FRONTEND_PORT}"
GMS_PORT = 8080
# GMS and Frontend share the pod network namespace, use distinct JMX-Prometheus ports
GMS_METRICS_PORT = 4318
FRONTEND_METRICS_PORT = 4319
# Consecutive `up`-check failures before pebble restarts a workload. At the 10s
# check period this is ~5 min, long enough that a slow-but-healthy JVM cold start
# is not killed mid-boot, short enough to promptly rescue a genuinely hung start.
HEALTHCHECK_FAILURE_THRESHOLD = 30

GMS_SERVICE_NAME = "datahub-gms"
ENTITY_REGISTRY_PATH = "/datahub/datahub-gms/resources/entity-registry.yml"
PEBBLE_BIN_PATH = "/charm/bin/pebble"
PEBBLE_SERVICE_ACTIVE = "active"
PEBBLE_SERVICE_BACKOFF = "backoff"

# GMS checks for the upgrade job's record every 10s for up to 1h, then exits for pebble to restart.
GMS_UPGRADE_POLL_MILLIS = 10000
GMS_UPGRADE_POLL_ATTEMPTS = 360

# The SystemUpdate job, run as a pebble service that pebble reruns with backoff until stopped.
UPGRADE_SERVICE_NAME = "datahub-upgrade"
UPGRADE_SCRIPT_PATH = "/charm-external/run-upgrade.sh"
UPGRADE_NOTICE_KEY = "canonical.com/datahub-k8s/upgrade-exited"
UPGRADE_TIMEOUT = "1h"  # turns a hung run into a failed one
UPGRADE_KILL_AFTER = "30s"  # SIGTERM grace before SIGKILL when the timeout fires
UPGRADE_BACKOFF_DELAY = "5m"  # first retry; pebble doubles it on each rerun
UPGRADE_BACKOFF_LIMIT = "30m"  # at most two runs an hour while drift persists

# Backend probes: bound how long a hook stalls on an unreachable backend.
KAFKA_PROBE_TIMEOUT_MS = 10000
OPENSEARCH_PROBE_TIMEOUT_SECONDS = 10
# Created by SystemUpdate on every backend; recheck on DataHub version bumps.
OPENSEARCH_SENTINELS = (
    "datahubpolicyindex_v2",
    "graph_service_v1",
    "system_metadata_service_v1",
    "datahub_usage_event",
)

# `kafka-topic-retention`: the topics whose retention the charm manages, keyed by the name that
# the option uses, with the environment variable that holds each topic's name. The upgrade history
# topic is left out: GMS waits at startup for the record of its version, which can be months old.
KAFKA_RETENTION_TOPICS = {
    "metadata-change-proposal": "METADATA_CHANGE_PROPOSAL_TOPIC_NAME",
    "failed-metadata-change-proposal": "FAILED_METADATA_CHANGE_PROPOSAL_TOPIC_NAME",
    "metadata-change-log-versioned": "METADATA_CHANGE_LOG_VERSIONED_TOPIC_NAME",
    "metadata-change-log-timeseries": "METADATA_CHANGE_LOG_TIMESERIES_TOPIC_NAME",
    "platform-event": "PLATFORM_EVENT_TOPIC_NAME",
    "usage-event": "DATAHUB_USAGE_EVENT_NAME",
}
KAFKA_RETENTION_KEYS = {"retention-ms": "retention.ms", "retention-bytes": "retention.bytes"}
KAFKA_RETENTION_MS_DEFAULT = 7 * 24 * 60 * 60 * 1000  # 7 days
KAFKA_RETENTION_DEFAULTS = {
    **{topic: {"retention-ms": KAFKA_RETENTION_MS_DEFAULT} for topic in KAFKA_RETENTION_TOPICS},
    # DataHub creates this topic with 90 days.
    "metadata-change-log-timeseries": {"retention-ms": 90 * 24 * 60 * 60 * 1000},
}

# OAuth/OIDC via the `oauth` relation (Canonical Identity Platform or an
# external IdP integrator). The callback path is fixed by the DataHub frontend.
OAUTH_RELATION_NAME = "oauth"
OAUTH_SCOPE = "openid profile email"
OAUTH_GRANT_TYPES = ["authorization_code"]
OIDC_CALLBACK_PATH = "/callback/oidc"

INIT_PWD_SECRET_LABEL = "datahub-init-pwd"  # nosec B105
ENCRYPTION_KEYS_SECRET_LABEL = "datahub-encryption-keys"  # nosec B105
SYSTEM_CLIENT_ID = "__datahub_system"
SYSTEM_CLIENT_SECRET_LABEL = "datahub-system-client-secret"  # nosec B105
SYSTEM_ACTOR_URN = f"urn:li:corpuser:{SYSTEM_CLIENT_ID}"
INGESTION_TOKEN_SECRET_LABEL = "datahub-ingestion-token"  # nosec B105
INGESTION_TOKEN_NAME = "juju-managed-ingestion-token"  # nosec B105
DEFAULT_EXECUTOR_ID = "default"

# `datahub_client` relation: one DataHub service account and one Juju secret
# holding its access token, per relation.
DATAHUB_CLIENT_RELATION_NAME = "datahub-client"
DATAHUB_CLIENT_SA_NAME_PREFIX = "[juju] "

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
PSQL_BIN_PATH = "/usr/bin/psql"

# `rc` starts at 143, so a run that pebble stops is not recorded as a success.
# `--foreground` keeps timeout in the service's process group. Pebble stops a service by
# signalling that group, so without it timeout and the JVM would keep running.
UPGRADE_SCRIPT = f"""#!/bin/bash
rc=143
trap '{PEBBLE_BIN_PATH} notify {UPGRADE_NOTICE_KEY} rc=$rc' EXIT
timeout --foreground --kill-after={UPGRADE_KILL_AFTER} {UPGRADE_TIMEOUT} \\
    {JAVA_BIN_PATH} -jar {UPGRADE_JAR_PATH} -u SystemUpdate
rc=$?
exit $rc
"""
