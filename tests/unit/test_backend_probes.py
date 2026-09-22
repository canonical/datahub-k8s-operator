# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for backend_probes.py."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from kafka.errors import KafkaTimeoutError
from kafka.structs import TopicPartition

import backend_probes
import exceptions
import literals

HISTORY = "DataHubUpgradeHistory_v1"
TOPICS = {"MetadataChangeProposal_v1", "PlatformEvent_v1", HISTORY}
KAFKA_CONN = {"bootstrap_server": "k1:9092,k2:9092", "username": "u", "password": "p"}  # nosec B105
OS_CONN = {"host": "os", "port": "9200", "username": "u", "password": "p", "tls-ca": "PEM"}  # nosec B105


def _history_record(version):
    """Return an upgrade history record value as SystemUpdate serializes it."""
    encoded = version.encode()
    return b"\x00\x00\x00\x00\x08" + bytes([len(encoded) * 2]) + encoded + b"\x00"


def _patch_kafka(topics, records):
    """Patch the Kafka clients to report ``topics`` and ``records`` in the history topic."""
    admin = MagicMock()
    admin.list_topics.return_value = list(topics)
    consumer = MagicMock()
    tp = TopicPartition(HISTORY, 0)
    consumer.partitions_for_topic.return_value = {0}
    consumer.beginning_offsets.return_value = {tp: 0}
    consumer.end_offsets.return_value = {tp: len(records)}
    consumer.poll.return_value = {tp: [SimpleNamespace(value=v) for v in records[-1:]]}
    return (
        patch.object(backend_probes, "KafkaAdminClient", return_value=admin),
        patch.object(backend_probes, "KafkaConsumer", return_value=consumer),
        consumer,
    )


class TestKafkaDrift:
    """Tests for backend_probes.kafka_drift."""

    def _drift(self, topics, records, version="1.4.0.5"):
        """Run kafka_drift against patched clients and return the drift and the consumer mock."""
        admin_patch, consumer_patch, consumer = _patch_kafka(topics, records)
        with admin_patch, consumer_patch:
            return backend_probes.kafka_drift(KAFKA_CONN, TOPICS, HISTORY, version), consumer

    def test_no_drift_when_topics_exist_and_latest_record_matches(self):
        """Nothing to report once SystemUpdate has run for the current version."""
        drift, _ = self._drift(TOPICS, [_history_record("v1.4.0.4-0"), _history_record("v1.4.0.5-0")])
        assert not drift

    def test_reads_only_the_latest_record(self):
        """The consumer is positioned on the last offset of the history topic."""
        _, consumer = self._drift(TOPICS, [_history_record("v1.4.0.4-0"), _history_record("v1.4.0.5-0")])
        consumer.seek.assert_called_once_with(TopicPartition(HISTORY, 0), 1)

    def test_missing_topics_are_reported(self):
        """Topics deleted after the bootstrap are listed by name."""
        drift, _ = self._drift({HISTORY}, [_history_record("v1.4.0.5-0")])
        assert drift == ["missing Kafka topics: MetadataChangeProposal_v1, PlatformEvent_v1"]

    def test_record_for_another_version_is_drift(self):
        """A rock bump leaves only the previous version's record, which GMS will not accept."""
        drift, _ = self._drift(TOPICS, [_history_record("v1.4.0.5-0")], version="1.5.0.1")
        assert drift == [f"no v1.5.0.1 record in {HISTORY}"]

    def test_version_prefix_does_not_match_a_longer_version(self):
        """v1.4.0.5 must not be satisfied by a v1.4.0.50 record."""
        drift, _ = self._drift(TOPICS, [_history_record("v1.4.0.50-0")])
        assert drift == [f"no v1.4.0.5 record in {HISTORY}"]

    def test_empty_history_topic_is_drift(self):
        """A recreated, empty history topic leaves GMS waiting at startup."""
        drift, consumer = self._drift(TOPICS, [])
        assert drift == [f"no v1.4.0.5 record in {HISTORY}"]
        consumer.poll.assert_not_called()

    def test_unknown_version_skips_the_record_check(self):
        """Without the rock version only topic existence is checked."""
        drift, consumer = self._drift(TOPICS, [], version=None)
        assert not drift
        consumer.partitions_for_topic.assert_not_called()

    def test_kafka_error_is_unreachable(self):
        """A client failure is reported as an unreachable backend, not as drift."""
        with patch.object(backend_probes, "KafkaAdminClient", side_effect=KafkaTimeoutError("Unable to bootstrap")):
            with pytest.raises(exceptions.BackendUnreachableError):
                backend_probes.kafka_drift(KAFKA_CONN, TOPICS, HISTORY, "1.4.0.5")


class TestKafkaSocketErrors:
    """Socket errors outside kafka-python's hierarchy are an unreachable backend."""

    def test_os_error_is_unreachable(self):
        """kafka-python can surface a raw socket error on connect."""
        with patch.object(backend_probes, "KafkaAdminClient", side_effect=OSError("network unreachable")):
            with pytest.raises(exceptions.BackendUnreachableError):
                backend_probes.kafka_drift(KAFKA_CONN, TOPICS, HISTORY, "1.4.0.5")


class TestOpenSearchDrift:
    """Tests for backend_probes.opensearch_drift.

    Attributes:
        ENTITIES: Entity names passed as the registry.
    """

    ENTITIES = ("dataset", "tag")

    @staticmethod
    def _response(body):
        """Return a successful fake HTTP response carrying ``body``."""
        return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)

    def _drift(self, present, settings, prefix=None, entities=ENTITIES):
        """Run opensearch_drift with ``present`` names resolved and ``settings`` returned."""
        resolved = {"indices": [{"name": n} for n in present], "aliases": [], "data_streams": []}
        with patch.object(
            backend_probes.requests, "get", side_effect=[self._response(resolved), self._response(settings)]
        ) as mock_get:
            return backend_probes.opensearch_drift(OS_CONN, prefix, entities), mock_get

    @staticmethod
    def _analysed(*names):
        """Return index settings for ``names`` as SystemUpdate builds them."""
        return {n: {"settings": {"index": {"provided_name": n, "analysis": {}}}} for n in names}

    def test_no_drift_when_sentinels_exist_and_indices_are_mapped(self):
        """Nothing to report on a backend SystemUpdate has built."""
        drift, _ = self._drift(literals.OPENSEARCH_SENTINELS, self._analysed("datasetindex_v2", "tagindex_v2"))
        assert not drift

    def test_missing_sentinels_are_reported(self):
        """A replaced or wiped cluster lacks the indices SystemUpdate always creates."""
        drift, _ = self._drift([], {})
        assert drift == [f"missing OpenSearch indices: {', '.join(literals.OPENSEARCH_SENTINELS)}"]

    def test_auto_created_index_is_reported(self):
        """An index created by a write to a missing index has no DataHub analysis settings."""
        settings = self._analysed("datasetindex_v2")
        settings["tagindex_v2"] = {"settings": {"index": {"provided_name": "tagindex_v2"}}}
        drift, _ = self._drift(literals.OPENSEARCH_SENTINELS, settings)
        assert drift == ["OpenSearch indices without DataHub mappings: tagindex_v2"]

    def test_only_registry_entity_indices_are_checked(self):
        """The mapping check names DataHub's entity indices, so other indices are never queried."""
        _, mock_get = self._drift(literals.OPENSEARCH_SENTINELS, {}, entities=["dataPlatform", "dataset"])
        settings_call = mock_get.call_args_list[1]
        assert settings_call.args[0].endswith("/dataplatformindex_v2,datasetindex_v2/_settings")
        assert settings_call.kwargs["params"]["ignore_unavailable"] == "true"

    def test_mapping_check_is_skipped_without_a_registry(self):
        """With no entity names the sentinels are still checked, and no settings query is made."""
        resolved = {"indices": [], "aliases": [], "data_streams": []}
        with patch.object(backend_probes.requests, "get", return_value=self._response(resolved)) as mock_get:
            drift = backend_probes.opensearch_drift(OS_CONN, None, None)
        assert mock_get.call_count == 1
        assert drift == [f"missing OpenSearch indices: {', '.join(literals.OPENSEARCH_SENTINELS)}"]

    def test_index_prefix_is_applied(self):
        """Sentinels and entity index names carry the configured prefix."""
        present = [f"dh_{n}" for n in literals.OPENSEARCH_SENTINELS]
        drift, mock_get = self._drift(present, {}, prefix="dh")
        assert not drift
        resolve_url = mock_get.call_args_list[0].args[0]
        settings_url = mock_get.call_args_list[1].args[0]
        assert resolve_url.endswith("/_resolve/index/" + ",".join(present))
        assert settings_url.endswith("/dh_datasetindex_v2,dh_tagindex_v2/_settings")

    def test_requests_verify_against_the_relation_ca(self):
        """TLS is verified with the CA bundle published on the relation."""
        _, mock_get = self._drift(literals.OPENSEARCH_SENTINELS, {})
        assert all(call.kwargs["verify"].endswith(".pem") for call in mock_get.call_args_list)

    def test_request_error_is_unreachable(self):
        """A connection failure is reported as an unreachable backend, not as drift."""
        with patch.object(backend_probes.requests, "get", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(exceptions.BackendUnreachableError):
                backend_probes.opensearch_drift(OS_CONN, None, self.ENTITIES)

    @pytest.mark.parametrize(
        "resolved",
        [ValueError("not JSON"), {"indices": [{"no_name": "x"}]}, ["not", "a", "mapping"]],
    )
    def test_unexpected_response_is_unreachable(self, resolved):
        """A body that is not OpenSearch's (a proxy page, another shape) is not a crash."""

        def _json():
            """Return the body, or raise like `Response.json` on one that is not JSON."""
            if isinstance(resolved, Exception):
                raise resolved
            return resolved

        response = SimpleNamespace(json=_json, raise_for_status=lambda: None)
        with patch.object(backend_probes.requests, "get", return_value=response):
            with pytest.raises(exceptions.BackendUnreachableError, match="unexpected response"):
                backend_probes.opensearch_drift(OS_CONN, None, self.ENTITIES)
