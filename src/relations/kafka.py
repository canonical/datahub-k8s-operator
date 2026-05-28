# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Kafka relation."""

import logging
from typing import Dict, Optional

from ops import framework

from log import log_event_handler

logger = logging.getLogger(__name__)


class KafkaRelation(framework.Object):
    """Client for datahub:kafka relations.

    Stateless: connection details are read live from the data_platform_libs
    requirer rather than cached in peer data.

    Attributes:
        charm: The charm this relation is attached to.
        connection: Live Kafka connection dict (bootstrap_server/user/pass) or None.
    """

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "kafka")
        self.charm = charm

        charm.framework.observe(charm.kafka.on.bootstrap_server_changed, self._on_kafka_changed)
        charm.framework.observe(charm.kafka.on.topic_created, self._on_kafka_changed)
        charm.framework.observe(charm.on.kafka_relation_broken, self._on_relation_broken)

    @property
    def connection(self) -> Optional[Dict[str, str]]:
        """Return the current Kafka connection details, or None when unrelated.

        Reads relation data on demand via data_platform_libs; the password
        comes from the juju secret published by the provider.
        """
        relations = list(self.charm.kafka.relations)
        if not relations:
            return None
        relation_id = relations[0].id
        try:
            data = self.charm.kafka.fetch_relation_data([relation_id]).get(relation_id, {})
        except Exception:  # noqa: BLE001 — lib raises in relation-broken events
            return None
        endpoints = data.get("endpoints")
        username = data.get("username")
        password = data.get("password")
        if not (endpoints and username and password):
            return None
        bootstrap_server = endpoints.split(",", 1)[0]
        return {
            "bootstrap_server": bootstrap_server,
            "username": username,
            "password": password,
        }

    @log_event_handler(logger)
    def _on_kafka_changed(self, event):
        """Handle bootstrap-server-changed / topic-created events.

        Args:
            event: The event triggered when Kafka changes.
        """
        self.charm.reconcile()

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with Kafka.

        Args:
            event: The event triggered when the relation is broken.
        """
        self.charm.reconcile()
