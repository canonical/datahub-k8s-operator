#!/bin/bash
# shellcheck disable=SC1000-SC9999
# ^ Ignore this file in `shellcheck` from the `test` CI workflow.
# This file comes from upstream DataHub with minimal changes.

OTEL_AGENT=""
if [[ $ENABLE_OTEL == true ]]; then
  OTEL_AGENT="-javaagent:opentelemetry-javaagent.jar "
fi

PROMETHEUS_AGENT=""
if [[ $ENABLE_PROMETHEUS == true ]]; then
  PROMETHEUS_AGENT="-javaagent:jmx_prometheus_javaagent.jar=4318:/datahub/datahub-gms/scripts/prometheus-config.yaml "
fi

JAVA_BIN="/usr/lib/jvm/java-17-openjdk-amd64/bin/java"
if [[ ! -x "$JAVA_BIN" ]]; then
  echo "Unable to find executable Java binary at ${JAVA_BIN}" >&2
  exit 1
fi

exec "$JAVA_BIN" $JAVA_OPTS $JMX_OPTS \
    $OTEL_AGENT \
    $PROMETHEUS_AGENT \
    -Dstats=unsecure \
    -jar /datahub/datahub-gms/bin/war.war
