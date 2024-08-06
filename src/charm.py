#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
from typing import Dict, List

import literals
import ops
import services
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequires,
    KafkaRequires,
    OpenSearchRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from log import log_event_handler
from relations.kafka import KafkaRelation
from relations.opensearch import OpenSearchRelation
from relations.postgresql import PostgresqlRelation
from state import State
from structured_config import CharmConfig

logger = logging.getLogger(__name__)


SERVICES: List[services.AbstractService] = [
    services.ActionsService,
    services.FrontendService,
    services.GMSService,
    services.MAEConsumerService,
    services.MCEConsumerService,
    services.OpensearchSetupService,
    services.KafkaSetupService,
    services.PostgresqlSetupService,
]


def get_pebble_layer(service: services.AbstractService, context: services.ServiceContext) -> Dict:
    """Create pebble layer based on service.

    Args:
        service: Name of DataHub service.
        context: Context from which to gather environment to include with the pebble plan.

    Returns:
        pebble plan dict.
    """
    layer = {
        "summary": f"DataHub layer for '{service.name}'",
        "services": {
            service.name: {
                "summary": f"DataHub layer for '{service.name}'",
                "command": service.command,
                "startup": "enabled" if service.is_enabled(context) else "disabled",
                "override": "replace",
            },
        },
    }

    env = service.compile_environment(context)
    if env is not None:
        layer["services"][service.name]["environment"] = env

    return layer


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

        # TODO (mertalpt): Can we make db/topic/index names dynamic to allow
        # the same dependency to be used by multiple DataHub deployments?

        # PostgreSQL
        self.db = DatabaseRequires(
            self, relation_name="db", database_name=literals.DB_NAME, extra_user_roles="admin"
        )
        self.db_relation = PostgresqlRelation(self)

        # Kafka
        self.kafka = KafkaRequires(
            self, relation_name="kafka", topic=literals.PLACEHOLDER_TOPIC, extra_user_roles="admin"
        )
        self.kafka_relation = KafkaRelation(self)

        # Opensearch
        self.opensearch = OpenSearchRequires(
            self,
            relation_name="opensearch",
            index=literals.PLACEHOLDER_INDEX,
            extra_user_roles="admin",
        )
        self.opensearch_relation = OpenSearchRelation(self)

        # Applications
        for application in SERVICES:
            self.framework.observe(self.on[application.name].pebble_ready, self._on_pebble_ready)

    @log_event_handler(logger)
    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Handle pebble-ready event."""
        # Frontend service requires a file to be present at startup.
        if event.workload.name == services.FrontendService.name:
            # TODO (mertalpt): Seek to make the default user configurable.
            event.workload.push(
                "/etc/datahub/plugins/frontend/auth/user.props",
                "datahub:datahub",
                make_dirs=True,
                permissions=0o644,
            )
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
        # Check all required relations exist
        relations = {
            "db": not bool(self._state.database_connection),
            "kafka": not bool(self._state.kafka_connection),
            "opensearch": not bool(self._state.opensearch_connection),
        }

        if any(relations.values()):
            missing_relations = [k for (k, v) in relations.items() if v]
            self.unit.status = ops.BlockedStatus(
                f"missing required relation(s): {', '.join(missing_relations)}"
            )
            return

        # Update services
        context = services.ServiceContext(self, self.config)

        for service in SERVICES:
            container = self.unit.get_container(service.name)
            if not container.can_connect():
                event.defer()
                return

            pebble_layer = get_pebble_layer(service, context)
            container.add_layer(service.name, pebble_layer, combine=True)
            container.replan()

        # Ports
        self.model.unit.set_ports(9002)

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(DatahubK8SOperatorCharm)  # type: ignore
