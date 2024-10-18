# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Kafka relation."""

import logging
from typing import Union

from charms.data_platform_libs.v0.data_interfaces import (
    BootstrapServerChangedEvent,
    TopicCreatedEvent,
)
from ops import WaitingStatus, framework

from log import log_event_handler

logger = logging.getLogger(__name__)


class KafkaRelation(framework.Object):
    """Client for datahub:kafka relations."""

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

    @log_event_handler(logger)
    def _on_kafka_changed(self, event: Union[BootstrapServerChangedEvent, TopicCreatedEvent]):
        """Handle Kafka change events.

        Args:
            event: The event triggered when Kafka changes.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            event.defer()
            return

        self.charm.unit.status = WaitingStatus(f"handling {event.relation.name} change")
        bootstrap_server = event.bootstrap_server.split(",", 1)[0]

        if self.charm._state.kafka_connection is None:
            self.charm._state.kafka_connection = {}

        conn = self.charm._state.kafka_connection
        conn["bootstrap_server"] = bootstrap_server
        conn["username"] = event.username
        conn["password"] = event.password

        # TODO (mertalpt): Check if it is possible to attach to an uninitialized Kafka deployment
        # without breaking the existing relation first.
        if conn.get("initialized") is None:
            conn["initialized"] = False

        self.charm._state.kafka_connection = conn
        self.charm._update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with Kafka.

        Args:
            event: The event triggered when the relation is broken.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            event.defer()
            return

        self.charm._state.kafka_connection = None
        self.charm._update(event)
