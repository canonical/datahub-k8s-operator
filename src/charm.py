#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application."""

import logging

import ops
from log import log_event_handler
from state import State

logger = logging.getLogger(__name__)


COMMANDS = {
    "datahub-actions": "/bin/sh -c /start_datahub_actions.sh",
    "datahub-frontend": "/bin/sh -c /start.sh",
    "datahub-gms": "/bin/sh -c /datahub/datahub-gms/scripts/start.sh",
    "datahub-mae-consumer": "/bin/sh -c /datahub/datahub-mae-consumer/scripts/start.sh",
    "datahub-mce-consumer": "/bin/sh -c /datahub/datahub-mce-consumer/scripts/start.sh",
}


CONTAINERS = list(COMMANDS.keys())


def get_pebble_layer(application_name, context):
    """Create pebble layer based on application.

    Args:
        application_name: Name of DataHub application.
        context: Environment to include with the pebble plan.

    Returns:
        pebble plan dict.
    """
    return {
        "summary": "datahub layer",
        "services": {
            application_name: {
                "summary": application_name,
                "command": COMMANDS[application_name],
                "startup": "enabled",
                "override": "replace",
                # Included to make configuration changes replan the service.
                "environment": context,
            },
        },
    }


class DatahubK8SOperatorCharm(ops.CharmBase):
    """Charm the application."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._state = State(self.app, lambda: self.model.get_relation("peer"))

        for container in CONTAINERS:
            self.framework.observe(self.on[container].pebble_ready, self._on_pebble_ready)

    @log_event_handler(logger)
    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Handle pebble-ready event."""
        self._update(event)

    # TODO (mertalpt): Fill this in.
    def _create_env(self):
        return {}

    def _update(self, event):
        """Update the DataHub configuration and replan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self._state.database_connections:
            self.unit.status = ops.BlockedStatus("missing db relation")
            return

        if not self._state.kafka_connections:
            self.unit.status = ops.BlockedStatus("missing kafka-client relation")
            return

        if not self._state.opensearch_connections:
            self.unit.status = ops.BlockedStatus("missing opensearch-client relation")
            return

        env = self._create_env()

        for service in CONTAINERS:
            container = self.unit.get_container(service)
            if not container.can_connect():
                event.defer()
                return

            pebble_layer = get_pebble_layer(service, env)
            container.add_layer(service, pebble_layer, combine=True)
            container.replan()

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(DatahubK8SOperatorCharm)  # type: ignore
