#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
from typing import Dict, List

import applications
import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequires,
    KafkaRequires,
    OpenSearchRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from literals import DB_NAME
from log import log_event_handler
from relations.kafka import KafkaRelation
from relations.opensearch import OpenSearchRelation
from relations.postgresql import PostgresqlRelation
from state import State
from structured_config import CharmConfig

logger = logging.getLogger(__name__)


APPLICATIONS: List[applications.AbstractApplication] = [
    applications.ActionsApplication,
    applications.FrontendApplication,
    applications.GMSApplication,
    applications.MAEConsumerApplication,
    applications.MCEConsumerApplication,
]


def get_pebble_layer(
    application: applications.AbstractApplication, context: applications.ApplicationContext
) -> Dict:
    """Create pebble layer based on application.

    Args:
        application: Name of DataHub application.
        context: Context from which to gather environment to include with the pebble plan.

    Returns:
        pebble plan dict.
    """
    return {
        "summary": "datahub layer",
        "services": {
            application.name: {
                "summary": application.name,
                "command": application.command,
                "startup": "enabled" if application.is_enabled(context) else "disabled",
                "override": "replace",
                # Included to make configuration changes replan the service.
                "environment": application.compile_environment(context),
            },
        },
    }


class DatahubK8SOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charm the application."""

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._state = State(self.app, lambda: self.model.get_relation("peer"))

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.peer_relation_changed, self._on_peer_relation_changed)
        # TODO (mertalpt): Decide whether to implement or remove this.
        # self.framework.observe(self.on.update_status, self._on_update_status)

        # PostgreSQL
        self.db = DatabaseRequires(
            self, relation_name="db", database_name=DB_NAME, extra_user_roles="admin"
        )
        self.db_relation = PostgresqlRelation(self)

        # Kafka
        self.kafka = KafkaRequires(
            self, relation_name="kafka", topic="datahub_dummy", extra_user_roles="admin"
        )
        self.kafka_relation = KafkaRelation(self)

        # Opensearch
        self.opensearch = OpenSearchRequires(
            self, relation_name="opensearch", index="datahub_dummy", extra_user_roles="admin"
        )
        self.opensearch_relation = OpenSearchRelation(self)

        # Applications
        for application in APPLICATIONS:
            self.framework.observe(self.on[application.name].pebble_ready, self._on_pebble_ready)

    @log_event_handler(logger)
    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Handle pebble-ready event."""
        self._update(event)

    @log_event_handler(logger)
    def _on_config_changed(self, event):
        """Handle changed configuration.

        Args:
            event: The event triggered when the configuration is changed.
        """
        self.unit.status = ops.WaitingStatus("configuring application")
        self._update(event)

    @log_event_handler(logger)
    def _on_peer_relation_changed(self, event):
        """Handle peer relation changed event.

        Args:
            event: The event triggered when the relation is changed.
        """
        self._update(event)

    @log_event_handler(logger)
    def _on_update_status(self, event):
        """Handle `update-status` events.

        Args:
            event: The `update-status` event that is triggered at regular intervals.
        """
        # TODO (mertalpt): Implement this if needed, otherwise remove it.
        pass

    def _update(self, event):
        """Update the DataHub configuration and replan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self._state.database_connection:
            self.unit.status = ops.BlockedStatus("missing required db relation")
            return

        if not self._state.kafka_connection:
            self.unit.status = ops.BlockedStatus("missing required kafka relation")
            return

        if not self._state.opensearch_connection:
            self.unit.status = ops.BlockedStatus("missing required opensearch relation")
            return

        context = applications.ApplicationContext(self, self.config)

        for application in APPLICATIONS:
            container = self.unit.get_container(application.name)
            if not container.can_connect():
                event.defer()
                return

            pebble_layer = get_pebble_layer(application, context)
            container.add_layer(application.name, pebble_layer, combine=True)
            container.replan()

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(DatahubK8SOperatorCharm)  # type: ignore
