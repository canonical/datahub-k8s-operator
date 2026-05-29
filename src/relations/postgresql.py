# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Postgresql relation."""

import logging
from typing import Dict, Optional

from ops import framework

import literals
from log import log_event_handler

logger = logging.getLogger(__name__)


class PostgresqlRelation(framework.Object):
    """Client for datahub:postgresql relations.

    Stateless: connection details are read live from the data_platform_libs
    requirer rather than cached in peer data.

    Attributes:
        charm: The charm this relation is attached to.
        connection: Live database connection dict (host/port/user/pass/dbname) or None.
    """

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

    @property
    def connection(self) -> Optional[Dict[str, str]]:
        """Return the current database connection details, or None when unrelated.

        Reads relation data on demand via data_platform_libs; the password
        comes from the juju secret published by the provider.
        """
        relations = list(self.charm.db.relations)
        if not relations:
            return None
        relation_id = relations[0].id
        try:
            data = self.charm.db.fetch_relation_data([relation_id]).get(relation_id, {})
        except Exception:  # noqa: BLE001 — lib raises in relation-broken events
            return None
        endpoints = data.get("endpoints")
        username = data.get("username")
        password = data.get("password")
        if not (endpoints and username and password):
            return None
        host, port = endpoints.split(",", 1)[0].split(":")
        return {
            "dbname": literals.DB_NAME,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }

    @log_event_handler(logger)
    def _on_database_changed(self, event) -> None:
        """Handle database-created / endpoints-changed events.

        Args:
            event: The event triggered when the relation changed.
        """
        self.charm.reconcile()

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with the database.

        Args:
            event: The event triggered when the relation is broken.
        """
        self.charm.reconcile()
