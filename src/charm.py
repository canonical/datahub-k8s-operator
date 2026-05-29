#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the application."""

import json
import logging
import secrets
from typing import Dict, List, Type, Union
from urllib.parse import urlparse

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequires,
    KafkaRequires,
    OpenSearchRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from ops.pebble import CheckStatus

import exceptions
import literals
import services
import utils
from log import log_event_handler
from relations.kafka import KafkaRelation
from relations.opensearch import OpenSearchRelation
from relations.postgresql import PostgresqlRelation
from relations.trino import TrinoRelation
from structured_config import CharmConfig

logger = logging.getLogger(__name__)


# Ordering of services has the function of setting the initialization priority.
SERVICES: List[Type[services.AbstractService]] = [
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
        svc_dict.update({"on-check-failure": {"up": "restart"}})
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
        config_type: Class used to store the config.
        system_client_id: The DataHub system client identifier for API auth.
        system_client_secret: The DataHub system client secret for API auth.
    """

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        """Construct.

        Args:
            framework: Object to initialize the charm instance.
        """
        super().__init__(framework)

        # Services
        for service in SERVICES:
            self.framework.observe(self.on[service.name].pebble_ready, self._on_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)
        self.framework.observe(self.on.peer_relation_changed, self._on_peer_relation_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.get_password_action, self._on_get_password_action)
        self.framework.observe(self.on.reindex_action, self._on_reindex_action)

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

        # Trino
        self.trino_relation = TrinoRelation(self)

        # Ingress. `strip_prefix=True` so Traefik strips the per-app path
        # prefix before forwarding, the frontend SPA and GMS REST endpoints
        # both expect to live at the root of their respective backend.
        self.frontend_ingress = IngressPerAppRequirer(
            self,
            relation_name="frontend-ingress",
            port=literals.FRONTEND_PORT,
            strip_prefix=True,
        )
        self.gms_ingress = IngressPerAppRequirer(
            self,
            relation_name="gms-ingress",
            port=literals.GMS_PORT,
            strip_prefix=True,
        )
        for ingress in (self.frontend_ingress, self.gms_ingress):
            self.framework.observe(ingress.on.ready, self._on_ingress_changed)
            self.framework.observe(ingress.on.revoked, self._on_ingress_changed)

    @property
    def system_client_id(self) -> str:
        """Return the DataHub system client identifier."""
        return literals.SYSTEM_CLIENT_ID

    @property
    def system_client_secret(self) -> str:
        """Return the DataHub system client secret."""
        return self._get_or_create_system_client_secret()

    @log_event_handler(logger)
    def _on_pebble_ready(self, event: ops.PebbleReadyEvent):
        """Handle pebble-ready event.

        Args:
            event: Event instance being handled.
        """
        self.reconcile()

    @log_event_handler(logger)
    def _on_reindex_action(self, event):
        """Run the 'RestoreIndices' command in 'datahub-upgrade' container.

        Args:
            event: The event triggered when the action is triggered.
        """
        if not self.unit.is_leader():
            event.fail("This action can only be run on the leader unit.")
            return

        context = services.ServiceContext(self)
        service = services.GMSService
        if not service.is_ready(context):
            event.fail("GMS service is not ready")
            return

        container = self.unit.get_container(service.name)
        if not container.can_connect():
            event.fail("cannot connect to container")
            return
        clean_index = event.params.get("clean", False)

        is_success = service.run_reindex(context, clean_index)

        result = {"result": "command failed"}
        if is_success:
            result = {
                "result": "command succeeded",
                "output": "Observe reindexing progress on 'datahub-gms' container logs.",
            }
        event.set_results(result)

    @log_event_handler(logger)
    def _on_get_password_action(self, event):
        """Return the auto-generated initial admin password.

        Args:
            event: The event triggered when the action is triggered.
        """
        try:
            password = self._get_password()
        except (ops.SecretNotFoundError, ops.ModelError) as e:
            event.fail(f"cannot read password secret: {e}")
            return

        if not password:
            event.fail("admin password has not been generated yet")
            return

        event.set_results({"password": password})

    @log_event_handler(logger)
    def _on_config_changed(self, event):
        """Handle changed configuration.

        Args:
            event: The event triggered when the configuration is changed.
        """
        self.reconcile()

    @log_event_handler(logger)
    def _on_secret_changed(self, event):
        """Handle secret-changed events for the configured encryption secret.

        Args:
            event: The secret-changed event fired by Juju.
        """
        encryption_keys_secret_id = self.config.encryption_keys_secret_id
        # ID from the event is in URI format that is painful to handle.
        # Worst case of a false positive is a redundant reconcile which is no issue.
        id_match = encryption_keys_secret_id and event.secret.id.endswith(encryption_keys_secret_id)
        label_match = event.secret.label == literals.ENCRYPTION_KEYS_SECRET_LABEL

        if id_match or label_match:
            self.reconcile()

    @log_event_handler(logger)
    def _on_peer_relation_changed(self, event):
        """Handle peer relation changed event.

        Args:
            event: The event triggered when the relation is changed.
        """
        self.reconcile()

    @log_event_handler(logger)
    def _on_update_status(self, event):  # noqa C901  # pylint: disable=R0915
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
                logger.info("cannot connect to service '%s'", service.name)
                is_not_ready = True
                continue
            logger.debug("performing up check for '%s'", service.name)
            try:
                check = container.get_check("up")
            except ops.ModelError:
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
            self.reconcile()
        elif is_not_ready:
            logger.info("services not ready, exiting to wait for the next update")
            self.unit.status = ops.MaintenanceStatus("status check: NOT READY")
        elif is_down:
            logger.info("services down, exiting to wait for the next update")
            self.unit.status = ops.MaintenanceStatus("status check: DOWN")
        else:
            self.reconcile()

    def _reconcile_trino_if_ready(self):
        """Run Trino ingestion reconciliation if preconditions are met."""
        if not self.unit.is_leader():
            return
        if not self.model.get_relation("trino-catalog"):
            return
        try:
            self.trino_relation.reconcile_ingestions()
        except Exception as e:
            logger.error("Trino reconciliation failed: %s", str(e))

    def _check_state(self):  # noqa: C901
        """Check the current state of the relations and overall charm readiness.

        Raises:
            ImproperSecretError: If the contents of the secret pointed to by
                'encryption-keys-secret-id' is malformed.
            UnreadyStateError: In case of invalid configuration, uninitialized relations,
                or an inaccessible 'encryption-keys-secret-id' secret.
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
        encryption_keys_secret_id = self.config.encryption_keys_secret_id
        try:
            encryption_keys_secret = self.model.get_secret(
                id=encryption_keys_secret_id, label=literals.ENCRYPTION_KEYS_SECRET_LABEL
            )
            content = encryption_keys_secret.get_content(refresh=True)
        except ops.SecretNotFoundError:
            raise exceptions.UnreadyStateError("secret for 'encryption-keys-secret-id': not found") from None
        except ops.ModelError:
            raise exceptions.UnreadyStateError("secret for 'encryption-keys-secret-id': permission denied") from None

        if "" in (
            content.get("gms-key", ""),
            content.get("frontend-key", ""),
        ):
            raise exceptions.ImproperSecretError(
                "secret pointed to by 'encryption-keys-secret-id' has improper contents"
            )

        # Check all required relations have published connection data.
        relations = {
            "db": self.db_relation.connection is None,
            "kafka": self.kafka_relation.connection is None,
            "opensearch": self.opensearch_relation.connection is None,
        }

        if any(relations.values()):
            missing_relations = [k for (k, v) in relations.items() if v]
            err = f"missing required relation(s): {', '.join(missing_relations)}"
            raise exceptions.UnreadyStateError(err)

        # Validate trino-patterns config is well-formed JSON mapping.
        # This is to be moved to a Pydantic validation if we switch to that.
        try:
            parsed = json.loads(self.config.trino_patterns)
        except (json.JSONDecodeError, TypeError) as e:
            raise exceptions.UnreadyStateError(f"invalid 'trino-patterns' config: {e}") from None
        if not isinstance(parsed, dict):
            raise exceptions.UnreadyStateError("invalid 'trino-patterns' config: must be a JSON object")

        if self.config.oidc_secret_id:
            self._check_oidc_requires_https()

    def _check_oidc_requires_https(self):
        """Ensure the frontend ingress URL is TLS-terminated when OIDC is enabled.

        Raises:
            UnreadyStateError: If the ingress is not ready or the URL is plain http.
        """
        fe_url = self.frontend_ingress.url if self.frontend_ingress.is_ready() else None
        if not fe_url:
            raise exceptions.UnreadyStateError("OIDC is enabled but the 'frontend-ingress' relation is not ready")
        parsed_url = urlparse(fe_url)
        if parsed_url.scheme != "https" and parsed_url.hostname != "localhost":
            raise exceptions.UnreadyStateError(
                "OIDC requires an HTTPS ingress URL; integrate the ingress with a certificates provider"
            )

    def _get_password(self):
        """Return the existing admin password, or None if it has not been generated yet.

        Stateless lookup where the secret is found via its label.
        """
        try:
            secret = self.model.get_secret(label=literals.INIT_PWD_SECRET_LABEL)
        except ops.SecretNotFoundError:
            return None
        return secret.get_content(refresh=True).get("password") or None

    def _ensure_password(self):
        """Return the admin password, generating one on the leader if it does not exist yet.

        Stateless secret creation, the leader uses a label so any unit can look it up.
        """
        password = self._get_password()
        if password:
            return password

        if self.unit.is_leader():
            content = {"password": secrets.token_urlsafe(24)}
            self.app.add_secret(content, label=literals.INIT_PWD_SECRET_LABEL)
            return content["password"]

        return ""

    def _get_or_create_system_client_secret(self):
        """Return the system client secret, creating it on the leader if it does not exist."""
        try:
            secret = self.model.get_secret(label=literals.SYSTEM_CLIENT_SECRET_LABEL)
            return secret.get_content(refresh=True).get("secret") or ""
        except ops.SecretNotFoundError:
            pass

        if self.unit.is_leader():
            content = {"secret": utils.generate_secret(64)}
            self.app.add_secret(content, label=literals.SYSTEM_CLIENT_SECRET_LABEL)
            return content["secret"]

        return ""

    @log_event_handler(logger)
    def _on_ingress_changed(self, event):
        """Re-render pebble plans so the OIDC base URL tracks the ingress URL.

        Args:
            event: The ingress ready/revoked event.
        """
        self.reconcile()

    def reconcile(self):
        """Reconcile the charm to its desired state.

        Single entry point for every observer. Reads current config + relation
        state, decides whether the charm is ready, then ensures every workload
        container is initialised and its pebble layer matches the expectations.
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
                    logger.info("Cannot connect to service '%s', skipping initialization", service.name)
                    return
                service.run_initialization(context)
        except Exception as e:
            logger.error("Failed to initialize service '%s': %s", service.name, str(e))
            self.unit.status = ops.BlockedStatus(str(e))
            return

        # Update services.
        for service in SERVICES:
            container = self.unit.get_container(service.name)
            if not container.can_connect():
                logger.info("Cannot connect to service '%s', skipping replan", service.name)
                return

            pebble_layer = get_pebble_layer(service, context)
            container.add_layer(service.name, pebble_layer, combine=True)
            container.replan()

        self._reconcile_trino_if_ready()

        self.unit.status = ops.ActiveStatus()


if __name__ == "__main__":  # pragma: nocover
    ops.main(DatahubK8SOperatorCharm)  # type: ignore
