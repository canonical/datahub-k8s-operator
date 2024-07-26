# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Postgresql relation."""

import logging
from typing import Union

import literals
from charms.data_platform_libs.v0.data_interfaces import (
    AuthenticationEvent,
    DatabaseEndpointsChangedEvent,
)
from exceptions import InitializationFailedError
from log import log_event_handler
from ops import MaintenanceStatus, WaitingStatus, framework
from ops.pebble import APIError, ExecError
from services import PostgresqlSetupService

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
    def _on_database_changed(
        self, event: Union[AuthenticationEvent, DatabaseEndpointsChangedEvent]
    ) -> None:
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

        if conn.get("initialized"):
            logger.debug("Skipping db initialization")
            return

        # Re-run idempotent initialization scripts.
        logger.debug("Running db initizalization scripts")
        self.charm.unit.status = MaintenanceStatus("initializing db")
        container = self.charm.unit.get_container(PostgresqlSetupService.name)
        stdout, stderr = None, None
        try:
            environment = {
                "POSTGRES_USERNAME": conn["username"],
                "POSTGRES_PASSWORD": conn["password"],
                "POSTGRES_HOST": conn["host"],
                "POSTGRES_PORT": conn["port"],
                "DATAHUB_DB_NAME": conn["dbname"],
            }
            process = container.exec(
                command=["/init.sh"],
                encoding="utf-8",
                timeout=300,
                environment=environment,
            )
            stdout, stderr = process.wait_output()
        except (APIError, ExecError):
            logger.debug("Failed db initialization")
            logger.debug("stdout: %s, stderr: %s", stdout, stderr)
            raise InitializationFailedError("failed to initialize db")
        else:
            logger.debug("Successful db initialization")
            conn["initialized"] = True

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
