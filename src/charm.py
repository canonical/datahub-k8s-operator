#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application."""

import logging
from typing import Dict, List, Type, Union

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequires,
    KafkaRequires,
    OpenSearchRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from ops.pebble import CheckStatus

import exceptions
import literals
import services
import utils
from log import log_event_handler
from relations.kafka import KafkaRelation
from relations.opensearch import OpenSearchRelation
from relations.postgresql import PostgresqlRelation
from state import State
from structured_config import CharmConfig

logger = logging.getLogger(__name__)


# Ordering of services has the function of setting the initialization priority.
SERVICES: List[Type[services.AbstractService]] = [
    services.PostgresqlSetupService,
    services.KafkaSetupService,
    services.OpensearchSetupService,
    services.UpgradeService,
    services.GMSService,
    services.FrontendService,
    services.ActionsService,
]


def get_pebble_layer(service: services.AbstractService, context: services.ServiceContext) -> Dict:
    """Create pebble layer based on service.

    Args:
        service: Name of DataHub service.
        context: Context from which to gather environment to include with the pebble plan.

    Returns:
        pebble plan dict.
    """
    svc_dict: Dict[str, Union[str, Dict[str, str]]] = {
        "summary": f"DataHub layer for '{service.name}'",
        "command": service.command,
        "startup": "enabled" if service.is_enabled(context) else "disabled",
        "override": "replace",
    }
    layer: Dict = {
        "services": {
            service.name: svc_dict,
        },
    }

    env = service.compile_environment(context)
    if env is not None:
        svc_dict["environment"] = env

    if service.healthcheck is not None:
        svc_dict.update(
            {
                "on-check-failure": {"up": "ignore"},
            }
        )
        layer.update(
            {
                "checks": {
                    "up": {
                        "override": "replace",
                        "period": "10s",
                        "threshold": 3,
                        "http": {
                            "url": f"http://localhost:{service.healthcheck['port']}{service.healthcheck['endpoint']}"
                        },
                    }
                }
            }
        )

    return layer


class DatahubK8SOperatorCharm(TypedCharmBase[CharmConfig]):
    """Charm the application.

    Attributes:
        _state: Used to store persistent data between invocations.
        config_type: Class used to store the config.
        external_fe_hostname: Property for the hostname of the frontend visible to outside.
        external_gms_hostname: Property for the hostname of the GMS visible to outside.
    """

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        """Construct.

        Args:
            framework: Object to initialize the charm instance.
        """
        super().__init__(framework)
        self._state = State(self.app, lambda: self.model.get_relation("peer"))

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.peer_relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)

        # TODO (mertalpt): Can we make db/topic/index names dynamic to allow
        # the same dependency to be used by multiple DataHub deployments?

        # PostgreSQL
        self.db = DatabaseRequires(self, relation_name="db", database_name=literals.DB_NAME, extra_user_roles="admin")
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

        # Ingress
        self._require_nginx_route()

        # Services
        for service in SERVICES:
            self.framework.observe(self.on[service.name].pebble_ready, self._on_pebble_ready)

    @property
    def external_fe_hostname(self):
        """Return the hostname used for external connections to the frontend."""
        return self.config["external-fe-hostname"] or f"{self.app.name}-frontend"

    @property
    def external_gms_hostname(self):
        """Return the hostname used for external connections to the GMS."""
        return self.config["external-gms-hostname"] or f"{self.app.name}-gms"

    def _require_nginx_route(self):
        """Require nginx-route relation based on the current configuration."""
        require_nginx_route(
            charm=self,
            service_hostname=self.external_fe_hostname,
            service_name=self.app.name,
            service_port=literals.FRONTEND_PORT,
            tls_secret_name=self.config["tls-secret-name"] or "",
            backend_protocol="HTTP",
            nginx_route_relation_name="nginx-fe-route",
        )

        require_nginx_route(
            charm=self,
            service_hostname=self.external_gms_hostname,
            service_name=self.app.name,
            service_port=literals.GMS_PORT,
            tls_secret_name=self.config["tls-secret-name"] or "",
            backend_protocol="HTTP",
            nginx_route_relation_name="nginx-gms-route",
        )

    @log_event_handler(logger)
    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Handle pebble-ready event.

        Args:
            event: Event instance being handled.
        """
        # Actions service requires a file to be altered at startup.
        if event.workload.name == services.ActionsService.name:
            utils.push_file(
                event.workload,
                ("src", "files", "executor.yaml"),
                "/etc/datahub/actions/system/conf/executor.yaml",
                0o644,
            )
        # Frontend service requires a file to be present at startup.
        if event.workload.name == services.FrontendService.name:
            self._state.frontend_truststore_initialized = False
            # TODO (mertalpt): Seek to make the default user configurable.
            utils.push_contents_to_file(
                event.workload,
                "datahub:datahub",
                "/etc/datahub/plugins/frontend/auth/user.props",
                0o644,
            )
        if event.workload.name == services.GMSService.name:
            self._state.gms_truststore_initialized = False

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
    def _on_update_status(self, event):  # noqa C901
        """Handle `update-status` events.

        Args:
            event: The `update-status` event that is triggered at regular intervals.
        """
        try:
            self._check_state()
        except (exceptions.UnreadyStateError, exceptions.ImproperSecretError) as err:
            self.unit.status = ops.BlockedStatus(str(err))
            return

        context = services.ServiceContext(self)
        is_down = False
        is_invalid = False
        is_not_ready = False
        for service in SERVICES:
            if service.healthcheck is None:
                continue

            if not service.is_enabled(context):
                logger.info("service '%s' is not ready", service.name)
                is_not_ready = True
                continue

            container = self.unit.get_container(service.name)
            if not container.can_connect():
                logger.info("cannot connect to service '%s'")
                is_not_ready = True
                continue
            logger.debug("performing up check for '%s'", service.name)
            try:
                check = container.get_check("up")
            except ops.model.ModelError:
                logger.info("invalid plan (missing check) for '%s'", service.name)
                is_invalid = True
                break  # guaranteed replan, exit loop
            else:
                plan = container.get_plan().to_dict()
                expected_plan = get_pebble_layer(service, context)
                # `get_plan` returns a `dict` subclass that messes with comparison.
                if dict(plan) != expected_plan:
                    logger.info("invalid plan (out of sync) for '%s'", service.name)
                    is_invalid = True
                    break  # guaranteed replan, exit loop
            if check.status != CheckStatus.UP:
                logger.info("up check failed for '%s'", service.name)
                is_down = True
                continue
            logger.debug("service '%s' is up", service.name)

        if is_invalid:
            logger.info("invalid plan detected, attempting replanning")
            self._update(event)
        elif is_not_ready:
            logger.info("services not ready, exiting to wait for the next update")
            self.unit.status = ops.MaintenanceStatus("status check: NOT READY")
        elif is_down:
            logger.info("services down, exiting to wait for the next update")
            self.unit.status = ops.MaintenanceStatus("status check: DOWN")
        else:
            self.unit.status = ops.ActiveStatus()

    def _check_state(self):
        """Check the current state of the relations and overall charm readiness.

        Raises:
            ImproperSecretError: If the contents of the secret pointed to by
                'encryption-keys-secret-id' is malformed.
            UnreadyStateError: In case of invalid configuration or uninitialized relations.
        """
        # Check all required configuration is set.
        configs = {
            "encryption-keys-secret-id": not self.config.encryption_keys_secret_id,
        }

        if any(configs.values()):
            missing_configs = [k for (k, v) in configs.items() if v]
            err = f"missing required configurations: {', '.join(missing_configs)}"
            raise exceptions.UnreadyStateError(err)

        # Validate secret schema.
        encryption_keys_secret = self.model.get_secret(id=self.config.encryption_keys_secret_id)

        # TODO (mertalpt): Handle this secret so that the application does not break
        # if it is changed.

        content = encryption_keys_secret.get_content(refresh=True)
        if "" in (
            content.get("gms-key", ""),
            content.get("frontend-key", ""),
        ):
            raise exceptions.ImproperSecretError(
                "secret pointed to by 'encryption-keys-secret-id' has improper contents"
            )

        # Check all required relations exist.
        relations = {
            "db": not self._state.database_connection,
            "kafka": not self._state.kafka_connection,
            "opensearch": not self._state.opensearch_connection,
        }

        if any(relations.values()):
            missing_relations = [k for (k, v) in relations.items() if v]
            err = f"missing required relation(s): {', '.join(missing_relations)}"
            raise exceptions.UnreadyStateError(err)

    def _update(self, event):
        """Update the DataHub configuration and replan its execution.

        Args:
            event: The event triggered when the relation changed.

        Raises:
            Exception: If an initialization fails for an unknown reason.
        """
        try:
            self._check_state()
        except (exceptions.UnreadyStateError, exceptions.ImproperSecretError) as err:
            self.unit.status = ops.BlockedStatus(str(err))
            return

        # Set ports.
        self.model.unit.set_ports(literals.FRONTEND_PORT, literals.GMS_PORT)

        context = services.ServiceContext(self)

        # Run initialization jobs.
        try:
            for service in SERVICES:
                container = self.unit.get_container(service.name)
                if not container.can_connect():
                    logger.info("Cannot connect to service '%s', deferring initialization", service.name)
                    event.defer()
                    return
                service.run_initialization(context)
        except Exception as e:
            # TODO (mertalpt): This is likely to result in an error loop,
            # can we solve it another way?
            logger.error("Failed to initialize service '%s' due to error: '%s'", service.name, str(e))
            raise

        # Update services.
        for service in SERVICES:
            container = self.unit.get_container(service.name)
            if not container.can_connect():
                logger.info("Cannot connect to service '%s', deferring replan", service.name)
                event.defer()
                return

            pebble_layer = get_pebble_layer(service, context)
            container.add_layer(service.name, pebble_layer, combine=True)
            container.replan()

        self.unit.status = ops.MaintenanceStatus("replanning application")


if __name__ == "__main__":  # pragma: nocover
    ops.main(DatahubK8SOperatorCharm)  # type: ignore
