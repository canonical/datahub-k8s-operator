# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Probe DataHub's backends to check the results of the SystemUpdate job."""

import logging
import tempfile
from typing import Dict, Iterable, List, Optional

import requests
from kafka.admin import KafkaAdminClient
from kafka.consumer import KafkaConsumer
from kafka.errors import KafkaError
from kafka.structs import TopicPartition

import exceptions
import literals

logging.getLogger("kafka").setLevel(logging.ERROR)


def _kafka_config(connection: Dict[str, str]) -> Dict:
    """Build kafka-python client settings matching the GMS Kafka client.

    Args:
        connection: Kafka relation connection details.

    Returns:
        Keyword arguments for kafka-python clients.
    """
    return {
        "bootstrap_servers": connection["bootstrap_server"].split(","),
        "security_protocol": "SASL_PLAINTEXT",
        "sasl_mechanism": "SCRAM-SHA-512",
        "sasl_plain_username": connection["username"],
        "sasl_plain_password": connection["password"],
        "request_timeout_ms": literals.KAFKA_PROBE_TIMEOUT_MS,
        "bootstrap_timeout_ms": literals.KAFKA_PROBE_TIMEOUT_MS,
    }


def _latest_records(config: Dict, topic: str) -> List[bytes]:
    """Return the value of the last record in each non-empty partition of a topic.

    Args:
        config: kafka-python client settings.
        topic: Topic to read.

    Returns:
        The latest record values.
    """
    consumer = KafkaConsumer(**config, enable_auto_commit=False)
    try:
        partitions = [TopicPartition(topic, p) for p in consumer.partitions_for_topic(topic) or ()]
        consumer.assign(partitions)
        start = consumer.beginning_offsets(partitions)
        end = consumer.end_offsets(partitions)
        non_empty = [tp for tp in partitions if end[tp] > start[tp]]
        if not non_empty:
            return []
        for tp in non_empty:
            consumer.seek(tp, end[tp] - 1)
        batches = consumer.poll(timeout_ms=literals.KAFKA_PROBE_TIMEOUT_MS)
    finally:
        consumer.close()
    return [batch[-1].value for batch in batches.values() if batch]


def kafka_drift(
    connection: Dict[str, str], topics: Iterable[str], history_topic: str, version: Optional[str]
) -> List[str]:
    """Describe what Kafka is missing compared to a completed SystemUpdate.

    SystemUpdate creates ``topics`` and appends a record for the DataHub version to
    ``history_topic``, which GMS waits for before it starts. The record carries the
    version as ``v<version>-<revision>``.

    Args:
        connection: Kafka relation connection details.
        topics: The topic names that SystemUpdate creates.
        history_topic: Name of the upgrade history topic.
        version: The DataHub version that GMS runs, without the leading ``v``.

    Returns:
        One description per problem found. Empty when Kafka has everything that SystemUpdate
        creates.

    Raises:
        BackendUnreachableError: If Kafka cannot be queried.
    """
    config = _kafka_config(connection)
    try:
        admin = KafkaAdminClient(**config)
        try:
            existing = set(admin.list_topics())
        finally:
            admin.close()

        drift = []
        missing = sorted(set(topics) - existing)
        if missing:
            drift.append(f"missing Kafka topics: {', '.join(missing)}")
        if version is not None and history_topic in existing:
            marker = f"v{version}-".encode()
            if not any(marker in value for value in _latest_records(config, history_topic)):
                drift.append(f"no v{version} record in {history_topic}")
        return drift
    except (KafkaError, OSError) as e:
        raise exceptions.BackendUnreachableError(f"cannot query Kafka: {e}") from e


def opensearch_drift(
    connection: Dict[str, str], index_prefix: Optional[str], entity_names: Optional[Iterable[str]]
) -> List[str]:
    """Describe what OpenSearch is missing compared to a completed SystemUpdate.

    SystemUpdate builds one search index per entity in the registry, named
    ``<prefix>_<entity>index_v2``, and gives each one DataHub's analysis settings. Search against
    a missing index fails. When something writes to a missing index, OpenSearch creates it
    automatically without those settings, and search against it fails too. Only the registry's
    index names are checked, so indices that belong to other applications in a shared cluster
    are never reported.

    Args:
        connection: OpenSearch relation connection details.
        index_prefix: The charm's `opensearch-index-prefix`, if set.
        entity_names: Entity names from DataHub's entity registry; None skips the mapping check.

    Returns:
        One description per problem found. Empty when OpenSearch has everything that
        SystemUpdate creates.

    Raises:
        BackendUnreachableError: If OpenSearch cannot be queried or answers unexpectedly.
    """
    prefix = f"{index_prefix}_" if index_prefix else ""
    base_url = f"https://{connection['host']}:{connection['port']}"
    auth = (connection["username"], connection["password"])
    sentinels = [f"{prefix}{name}" for name in literals.OPENSEARCH_SENTINELS]
    entity_indices = [f"{prefix}{name.lower()}index_v2" for name in entity_names or ()]

    with tempfile.NamedTemporaryFile("w", suffix=".pem") as ca_file:
        ca_file.write(connection["tls-ca"])
        ca_file.flush()
        try:
            resolved = requests.get(
                f"{base_url}/_resolve/index/{','.join(sentinels + entity_indices)}",
                auth=auth,
                verify=ca_file.name,
                timeout=literals.OPENSEARCH_PROBE_TIMEOUT_SECONDS,
            )
            resolved.raise_for_status()
            # OpenSearch replies with the names that it found, in three groups: "indices",
            # "aliases" and "data_streams". It leaves a name out when that name does not exist,
            # so a name missing from this set is missing from the cluster. An index that
            # BuildIndices reindexed keeps its old name as an alias, and OpenSearch lists that
            # alias under the old name, so the probe still counts the index as present.
            present = {entry["name"] for entries in resolved.json().values() for entry in entries}
            unmapped: List[str] = []
            if entity_indices:
                settings = requests.get(
                    f"{base_url}/{','.join(entity_indices)}/_settings",
                    params={
                        "filter_path": "*.settings.index.provided_name,*.settings.index.analysis.normalizer",
                        "ignore_unavailable": "true",
                    },
                    auth=auth,
                    verify=ca_file.name,
                    timeout=literals.OPENSEARCH_PROBE_TIMEOUT_SECONDS,
                )
                settings.raise_for_status()
                unmapped = sorted(
                    name for name, body in settings.json().items() if "analysis" not in body["settings"]["index"]
                )
        except requests.RequestException as e:
            raise exceptions.BackendUnreachableError(f"cannot query OpenSearch: {e}") from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise exceptions.BackendUnreachableError(f"unexpected response from OpenSearch: {e!r}") from e

    drift = []
    missing = [name for name in sentinels + entity_indices if name not in present]
    if missing:
        drift.append(f"missing OpenSearch indices: {', '.join(missing)}")
    if unmapped:
        drift.append(f"OpenSearch indices without DataHub mappings: {', '.join(unmapped)}")
    return drift
