# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Probe DataHub's backends for the results of the SystemUpdate job, and set topic retention."""

import logging
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

import requests
from kafka.admin import (
    AlterConfigOp,
    ConfigResource,
    ConfigResourceType,
    KafkaAdminClient,
)
from kafka.consumer import KafkaConsumer
from kafka.errors import KafkaError
from kafka.structs import TopicPartition

import exceptions
import literals

logging.getLogger("kafka").setLevel(logging.ERROR)


@dataclass(frozen=True)
class KafkaClientConfig:
    """kafka-python client settings matching the GMS Kafka client.

    Attributes:
        bootstrap_servers: Broker addresses to bootstrap from.
        sasl_plain_username: SASL username.
        sasl_plain_password: SASL password, left out of the repr so that it is never logged.
        security_protocol: Kafka security protocol.
        sasl_mechanism: SASL mechanism.
        request_timeout_ms: Timeout for each request.
        bootstrap_timeout_ms: Timeout for the first connection to the brokers.
    """

    bootstrap_servers: List[str]
    sasl_plain_username: str
    sasl_plain_password: str = field(repr=False)
    security_protocol: str = "SASL_PLAINTEXT"
    sasl_mechanism: str = "SCRAM-SHA-512"
    request_timeout_ms: int = literals.KAFKA_PROBE_TIMEOUT_MS
    bootstrap_timeout_ms: int = literals.KAFKA_PROBE_TIMEOUT_MS

    @classmethod
    def from_connection(cls, connection: Dict[str, str]) -> "KafkaClientConfig":
        """Build the settings from the Kafka relation's connection details.

        Args:
            connection: Kafka relation connection details.

        Returns:
            The client settings.
        """
        return cls(
            bootstrap_servers=connection["bootstrap_server"].split(","),
            sasl_plain_username=connection["username"],
            sasl_plain_password=connection["password"],
        )

    def kwargs(self) -> Dict:
        """Return the settings as keyword arguments for the kafka-python clients.

        Returns:
            Keyword arguments for ``KafkaAdminClient`` and ``KafkaConsumer``.
        """
        return asdict(self)


def _latest_records(config: KafkaClientConfig, topic: str) -> List[bytes]:
    """Return the value of the last record in each non-empty partition of a topic.

    Args:
        config: kafka-python client settings.
        topic: Topic to read.

    Returns:
        The latest record values.
    """
    consumer = KafkaConsumer(**config.kwargs(), enable_auto_commit=False)
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
    config = KafkaClientConfig.from_connection(connection)
    try:
        admin = KafkaAdminClient(**config.kwargs())
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


def kafka_set_retention(connection: Dict[str, str], retention: Dict[str, Dict[str, Optional[str]]]) -> List[str]:
    """Set the retention configs of the topics that exist, and leave their other configs alone.

    Args:
        connection: Kafka relation connection details.
        retention: For each topic, the value of each retention config to set. None removes the
            topic's own value, so that the broker default applies.

    Returns:
        One description per topic that was changed.

    Raises:
        BackendUnreachableError: If Kafka cannot be queried or rejects a change.
    """
    try:
        admin = KafkaAdminClient(**_kafka_config(connection))
        try:
            topics = sorted(set(retention) & set(admin.list_topics()))
            if not topics:
                return []
            described = admin.describe_configs(
                [ConfigResource(ConfigResourceType.TOPIC, topic, list(retention[topic])) for topic in topics]
            ).get("topic", {})
            changes = {}
            for topic in topics:
                # `describe_configs` filters on `modified` by default, so it returns the
                # values that are set on the topic and not the ones it inherits from the broker.
                current = {key: config["value"] for key, config in described.get(topic, {}).items()}
                updates = {
                    key: (AlterConfigOp.SET, value) if value is not None else (AlterConfigOp.DELETE, None)
                    for key, value in retention[topic].items()
                    if current.get(key) != value
                }
                if updates:
                    changes[topic] = updates
            if not changes:
                return []
            results = admin.alter_configs(
                [ConfigResource(ConfigResourceType.TOPIC, topic, updates) for topic, updates in changes.items()],
                incremental=True,
            ).get("topic", {})
        finally:
            admin.close()
    except (KafkaError, OSError) as e:
        raise exceptions.BackendUnreachableError(f"cannot set Kafka topic retention: {e}") from e

    failed = {topic: result for topic, result in results.items() if result != "OK"}
    if failed:
        raise exceptions.BackendUnreachableError(f"cannot set Kafka topic retention: {failed}")
    return [
        f"{topic}: "
        + ", ".join(f"{key}={value}" if value is not None else f"{key} removed" for key, (_, value) in updates.items())
        for topic, updates in sorted(changes.items())
    ]


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
