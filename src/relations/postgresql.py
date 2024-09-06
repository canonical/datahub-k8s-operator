# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Postgresql relation."""

import logging
from typing import Union

from charms.data_platform_libs.v0.data_interfaces import (
    AuthenticationEvent,
    DatabaseEndpointsChangedEvent,
)
from ops import WaitingStatus, framework

import literals
from log import log_event_handler

logger = logging.getLogger(__name__)


class PostgresqlRelation(framework.Object):
    """Client for datahub:postgresql relations."""

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "db")
        self.charm = charm

        charm.framework.observe(charm.db.on.database_created, self._on_database_changed)
        charm.framework.observe(charm.db.on.endpoints_changed, self._on_database_changed)
        charm.framework.observe(charm.on.db_relation_broken, self._on_relation_broken)

    @log_event_handler(logger)
    def _on_database_changed(self, event: Union[AuthenticationEvent, DatabaseEndpointsChangedEvent]) -> None:
        """Handle database creation/change events.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            event.defer()
            return

        self.charm.unit.status = WaitingStatus(f"handling {event.relation.name} change")
        host, port = event.endpoints.split(",", 1)[0].split(":")

        if self.charm._state.database_connection is None:
            self.charm._state.database_connection = {}

        conn = self.charm._state.database_connection
        conn["dbname"] = literals.DB_NAME
        conn["host"] = host
        conn["port"] = port
        conn["username"] = event.username
        conn["password"] = event.password

        # TODO (mertalpt): Check if it is possible to attach to an uninitialized
        # PostgreSQL deployment without breaking the existing relation first.
        if conn.get("initialized") is None:
            conn["initialized"] = False

        self.charm._state.database_connection = conn
        self.charm._update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with the database.

        Args:
            event: The event triggered when the relation is broken.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            event.defer()
            return

        self.charm._state.database_connection = None
        self.charm._update(event)
