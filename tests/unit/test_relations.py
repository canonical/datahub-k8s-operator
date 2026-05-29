# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the stateless relation modules (postgresql, kafka, opensearch)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import literals
from relations.kafka import KafkaRelation
from relations.opensearch import OpenSearchRelation
from relations.postgresql import PostgresqlRelation


def _charm_with_relation(lib_attr, *, relation_data, relation_present=True, raises=False):
    """Build a MagicMock charm whose data-platform lib returns the given relation data.

    Args:
        lib_attr: The charm attribute name for the lib (e.g. "db", "kafka", "opensearch").
        relation_data: The dict fetch_relation_data should return for relation id 1.
        relation_present: Whether the lib reports an active relation.
        raises: If True, fetch_relation_data raises (simulates a relation-broken event).
    """
    charm = MagicMock()
    lib = getattr(charm, lib_attr)
    if relation_present:
        lib.relations = [SimpleNamespace(id=1)]
    else:
        lib.relations = []
    if raises:
        lib.fetch_relation_data.side_effect = RuntimeError("cannot fetch in relation-broken")
    else:
        lib.fetch_relation_data.return_value = {1: relation_data}
    return charm


class TestPostgresqlConnection:
    """Tests for PostgresqlRelation.connection."""

    @staticmethod
    def _relation(charm):
        """Build a PostgresqlRelation bound to charm without running __init__."""
        rel = PostgresqlRelation.__new__(PostgresqlRelation)
        rel.charm = charm
        return rel

    def test_returns_dict_when_data_present(self):
        """Endpoints are parsed into host/port and credentials are included."""
        charm = _charm_with_relation(
            "db",
            relation_data={
                "endpoints": "pg-host:5432,replica:5432",
                "username": "u",
                "password": "p",  # nosec B105
            },
        )
        conn = self._relation(charm).connection
        assert conn == {
            "dbname": literals.DB_NAME,
            "host": "pg-host",
            "port": "5432",
            "username": "u",
            "password": "p",  # nosec B105
        }

    def test_returns_none_when_unrelated(self):
        """None is returned when no relation exists."""
        charm = _charm_with_relation("db", relation_data={}, relation_present=False)
        assert self._relation(charm).connection is None

    def test_returns_none_when_data_incomplete(self):
        """None is returned when credentials haven't been published yet."""
        charm = _charm_with_relation("db", relation_data={"endpoints": "pg-host:5432"})
        assert self._relation(charm).connection is None

    def test_returns_none_when_fetch_raises(self):
        """None is returned when fetch_relation_data raises (relation-broken)."""
        charm = _charm_with_relation("db", relation_data={}, raises=True)
        assert self._relation(charm).connection is None

    def test_handlers_call_reconcile(self):
        """Both changed and broken handlers delegate to charm.reconcile()."""
        charm = _charm_with_relation("db", relation_data={})
        rel = self._relation(charm)
        rel._on_database_changed(MagicMock())
        rel._on_relation_broken(MagicMock())
        assert charm.reconcile.call_count == 2


class TestKafkaConnection:
    """Tests for KafkaRelation.connection."""

    @staticmethod
    def _relation(charm):
        """Build a KafkaRelation bound to charm without running __init__."""
        rel = KafkaRelation.__new__(KafkaRelation)
        rel.charm = charm
        return rel

    def test_returns_dict_when_data_present(self):
        """The first bootstrap server is taken and credentials are included."""
        charm = _charm_with_relation(
            "kafka",
            relation_data={
                "endpoints": "kafka-a:9092,kafka-b:9092",
                "username": "u",
                "password": "p",  # nosec B105
            },
        )
        conn = self._relation(charm).connection
        assert conn == {
            "bootstrap_server": "kafka-a:9092",
            "username": "u",
            "password": "p",  # nosec B105
        }

    def test_returns_none_when_unrelated(self):
        """None is returned when no relation exists."""
        charm = _charm_with_relation("kafka", relation_data={}, relation_present=False)
        assert self._relation(charm).connection is None

    def test_returns_none_when_fetch_raises(self):
        """None is returned when fetch_relation_data raises (relation-broken)."""
        charm = _charm_with_relation("kafka", relation_data={}, raises=True)
        assert self._relation(charm).connection is None

    def test_handlers_call_reconcile(self):
        """Both changed and broken handlers delegate to charm.reconcile()."""
        charm = _charm_with_relation("kafka", relation_data={})
        rel = self._relation(charm)
        rel._on_kafka_changed(MagicMock())
        rel._on_relation_broken(MagicMock())
        assert charm.reconcile.call_count == 2


class TestOpenSearchConnection:
    """Tests for OpenSearchRelation.connection."""

    @staticmethod
    def _relation(charm):
        """Build an OpenSearchRelation bound to charm without running __init__."""
        rel = OpenSearchRelation.__new__(OpenSearchRelation)
        rel.charm = charm
        return rel

    def test_returns_dict_when_data_present(self):
        """Host/port are parsed and credentials plus tls-ca are included."""
        charm = _charm_with_relation(
            "opensearch",
            relation_data={
                "endpoints": "os-host:9200",
                "username": "u",
                "password": "p",  # nosec B105
                "tls-ca": "PEM",
            },
        )
        conn = self._relation(charm).connection
        assert conn == {
            "host": "os-host",
            "port": "9200",
            "username": "u",
            "password": "p",  # nosec B105
            "tls-ca": "PEM",
        }

    def test_returns_none_when_tls_ca_missing(self):
        """None is returned until the tls-ca has been published."""
        charm = _charm_with_relation(
            "opensearch",
            relation_data={
                "endpoints": "os-host:9200",
                "username": "u",
                "password": "p",  # nosec B105
            },
        )
        assert self._relation(charm).connection is None

    def test_returns_none_when_unrelated(self):
        """None is returned when no relation exists."""
        charm = _charm_with_relation("opensearch", relation_data={}, relation_present=False)
        assert self._relation(charm).connection is None

    def test_returns_none_when_fetch_raises(self):
        """None is returned when fetch_relation_data raises (relation-broken)."""
        charm = _charm_with_relation("opensearch", relation_data={}, raises=True)
        assert self._relation(charm).connection is None

    def test_handlers_call_reconcile(self):
        """Both changed and broken handlers delegate to charm.reconcile()."""
        charm = _charm_with_relation("opensearch", relation_data={})
        rel = self._relation(charm)
        rel._on_opensearch_changed(MagicMock())
        rel._on_relation_broken(MagicMock())
        assert charm.reconcile.call_count == 2
