# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Kafka relation."""

import logging
from typing import Union

import services
import utils
from charms.data_platform_libs.v0.data_interfaces import (
    BootstrapServerChangedEvent,
    TopicCreatedEvent,
)
from exceptions import InitializationFailedError
from log import log_event_handler
from ops import MaintenanceStatus, WaitingStatus, framework
from ops.pebble import APIError, ExecError
from services import KafkaSetupService

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

        if conn.get("initialized"):
            logger.debug("Skipping kafka initialization")
            return

        # Re-run idempotent initialization scripts.
        logger.debug("Running kafka initialization scripts")
        self.charm.unit.status = MaintenanceStatus("initializing kafka")
        container = self.charm.unit.get_container(KafkaSetupService.name)
        stdout, stderr = None, None
        environment = {
            "KAFKA_BOOTSTRAP_SERVER": conn["bootstrap_server"],
            # The value for this is not actually used in the container.
            "KAFKA_ZOOKEEPER_CONNECT": "",
            "MAX_MESSAGE_BYTES": "5242880",
            "USE_CONFLUENT_SCHEMA_REGISTRY": "false",
            # TODO (mertalpt): Uncomment and find appropriate values. Might be related to units of Kafka.
            # "PARTITIONS": "",
            # "REPLICATION_FACTOR": "",
            # TODO (mertalpt): Figure out how to switch these based on Kafka's SSL status.
            "KAFKA_PROPERTIES_SECURITY_PROTOCOL": "SASL_PLAINTEXT",
            "KAFKA_PROPERTIES_SASL_MECHANISM": "SCRAM-SHA-512",
            "KAFKA_PROPERTIES_SASL_JAAS_CONFIG": (
                "org.apache.kafka.common.security.scram.ScramLoginModule required "
                f'username="{conn["username"]}" password="{conn["password"]}";'
            ),
        }
        topic_names = services._kafka_topic_names(self.charm.config.kafka_topic_prefix)
        environment.update(topic_names)
        init_script_path = utils.get_abs_path("src", "scripts", "generic-init.sh")
        with open(init_script_path, "r") as fp:
            init_file_content = fp.read()
        try:
            container.push("/generic-init.sh", init_file_content, permissions=0o755)
            process = container.exec(
                command=["/generic-init.sh", "/opt/kafka/kafka-setup.sh"],
                working_dir="/opt/kafka",
                encoding="utf-8",
                timeout=120,
                environment=environment,
            )
            stderr, stdout = process.wait_output()
        except (APIError, ExecError):
            logger.debug(
                "Failed kafka initialization:: stdout: '%s' - stderr: '%s'", stdout, stderr
            )
            raise InitializationFailedError("failed to initialize kafka")

        logger.debug("Successful kafka initialization")
        conn["initialized"] = True
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
