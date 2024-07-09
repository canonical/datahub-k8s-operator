# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Opensearch relation."""

import logging
from typing import Union

from charms.data_platform_libs.v0.data_interfaces import (
    AuthenticationEvent,
    DatabaseEndpointsChangedEvent,
    IndexCreatedEvent,
)
from ops import WaitingStatus, framework

from log import log_event_handler

logger = logging.getLogger(__name__)


class OpenSearchRelation(framework.Object):
    """Client for datahub:opensearch relations."""

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "opensearch")
        self.charm = charm

        charm.framework.observe(charm.opensearch.on.endpoints_changed, self._on_opensearch_changed)
        charm.framework.observe(charm.opensearch.on.index_created, self._on_opensearch_changed)
        charm.framework.observe(charm.on.opensearch_relation_broken, self._on_relation_broken)

    @log_event_handler(logger)
    def _on_opensearch_changed(
        self, event: Union[DatabaseEndpointsChangedEvent, IndexCreatedEvent, AuthenticationEvent]
    ) -> None:
        """Handle Opensearch change events.

        Args:
            event: The event triggered when Opensearch changes.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            event.defer()
            return

        self.charm.unit.status = WaitingStatus(f"handling {event.relation.name} change")
        host, port = event.endpoints.split(",", 1)[0].split(":")

        if self.charm._state.opensearch_connection is None:
            self.charm._state.opensearch_connection = {}

        conn = self.charm._state.opensearch_connection
        conn["host"] = host
        conn["port"] = port
        conn["username"] = event.username
        conn["password"] = event.password
        conn["tls-ca"] = event.tls_ca

        # TODO (mertalpt): Check if it is possible to attach to an uninitialized Opensearch deployment
        # without breaking the existing relation first.
        if conn.get("initialized") is None:
            conn["initialized"] = False

        self.charm._state.opensearch_connection = conn
        self.charm._update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with Opensearch.

        Args:
            event: The event triggered when the relation is broken.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            event.defer()
            return

        self.charm._state.opensearch_connection = None
        self.charm._update(event)
