# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Opensearch relation."""

import logging
from typing import Union

import services
import utils
from charms.data_platform_libs.v0.data_interfaces import (
    AuthenticationEvent,
    DatabaseEndpointsChangedEvent,
    IndexCreatedEvent,
)
from exceptions import InitializationFailedError
from log import log_event_handler
from ops import MaintenanceStatus, WaitingStatus, framework
from ops.pebble import APIError, ExecError

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

        if conn.get("initialized"):
            logger.debug("Skipping opensearch initialization")
            return
        
        ca_certs = [cert.strip() + '\n-----END CERTIFICATE-----' for cert in conn["tls-ca"].split('-----END CERTIFICATE-----') if cert.strip()]
        if len(ca_certs) != 2:
            logger.debug("Unexpected 'tls-ca' format: expected '2' certificates, got '%d'", len(ca_certs))
            raise InitializationFailedError("unexpected 'tls-ca' format")

        # Re-run idempotent initialization scripts.
        logger.debug("Running opensearch initialization scripts")
        self.charm.unit.status = MaintenanceStatus("initializing opensearch")
        container = self.charm.unit.get_container(services.OpensearchSetupService.name)
        stdout, stderr = None, None
        environment = {
            "ELASTICSEARCH_HOST": conn["host"],
            "ELASTICSEARCH_PORT": conn["port"],
            "SKIP_ELASTICSEARCH_CHECK": "false",
            "ELASTICSEARCH_INSECURE": "false",
            "ELASTICSEARCH_USE_SSL": "true",
            "ELASTICSEARCH_USERNAME": conn["username"],
            "ELASTICSEARCH_PASSWORD": conn["password"],
            "INDEX_PREFIX": self.charm.config.opensearch_index_prefix,
            "DATAHUB_ANALYTICS_ENABLED": "true",
            "USE_AWS_ELASTICSEARCH": "true",
        }
        init_script_path = utils.get_abs_path("src", "scripts", "generic-init.sh")
        with open(init_script_path, "r") as fp:
            init_file_content = fp.read()
        os_script_path = utils.get_abs_path("src", "scripts", "create-indices.sh")
        with open(os_script_path, "r") as fp:
            os_file_content = fp.read()
        try:
            container.push("/ca-cert.pem", conn["tls-ca"], permissions=0o644)
            container.push("/generic-init.sh", init_file_content, permissions=0o755)
            container.push("/create-indices.sh", os_file_content, permissions=0o755)
            process = container.exec(
                command=["/generic-init.sh", "/create-indices.sh"],
                encoding="utf-8",
                timeout=120,
                environment=environment,
            )
            stdout, stderr = process.wait_output()
        except (APIError, ExecError):
            logger.debug(
                "Failed opensearch initialization:: stdout: '%s' - stderr: '%s'", stdout, stderr
            )
            raise InitializationFailedError("failed to initialize opensearch")
        else:
            logger.debug("Successful opensearch initialization")

        # TODO (mertalpt): Make truststore parameters configurable and secure.
        # Initialize truststores for consumer services
        logger.debug("Running truststore initialization scripts")
        self.charm.unit.status = MaintenanceStatus("initializing truststores for opensearch")
        container = self.charm.unit.get_container(services.GMSService.name)
        stdout, stderr = None, None
        environment = {
            "STORE_PASS": "foobar",
        }
        init_script_path = utils.get_abs_path("src", "scripts", "init-truststore.sh")
        with open(init_script_path, "r") as fp:
            init_file_content = fp.read()
        try:
            container.push("/ca-cert.pem", conn["tls-ca"], permissions=0o644)
            container.push("/init-truststore.sh", init_file_content, permissions=0o755)
            process = container.exec(["/init-truststore.sh"], environment=environment)
            stdout, stderr = process.wait_output()
        except (APIError, ExecError):
            logger.debug(
                "Failed truststore initialization:: stdout '%s' - stderr '%s'", stdout, stderr
            )
            raise InitializationFailedError("failed to initialize truststores for opensearch")
        else:
            logger.debug("Successful truststore initialization")

        conn["initialized"] = True
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
