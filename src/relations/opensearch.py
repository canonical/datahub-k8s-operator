# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Opensearch relation."""

import logging
from typing import Dict, Optional

from ops import framework

from log import log_event_handler

logger = logging.getLogger(__name__)


class OpenSearchRelation(framework.Object):
    """Client for datahub:opensearch relations.

    Stateless: connection details are read live from the data_platform_libs
    requirer rather than cached in peer data.

    Attributes:
        charm: The charm this relation is attached to.
        connection: Live OpenSearch connection dict (host/port/user/pass/tls-ca) or None.
    """

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

    @property
    def connection(self) -> Optional[Dict[str, str]]:
        """Return the current OpenSearch connection details, or None when unrelated.

        Reads relation data on demand via data_platform_libs; the password
        comes from the juju secret published by the provider.
        """
        relations = list(self.charm.opensearch.relations)
        if not relations:
            return None
        relation_id = relations[0].id
        try:
            data = self.charm.opensearch.fetch_relation_data([relation_id]).get(relation_id, {})
        except Exception:  # noqa: BLE001 — lib raises in relation-broken events
            return None
        endpoints = data.get("endpoints")
        username = data.get("username")
        password = data.get("password")
        tls_ca = data.get("tls-ca")
        if not (endpoints and username and password and tls_ca):
            return None
        host, port = endpoints.split(",", 1)[0].split(":")
        return {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "tls-ca": tls_ca,
        }

    @log_event_handler(logger)
    def _on_opensearch_changed(self, event) -> None:
        """Handle endpoints-changed / index-created events.

        Args:
            event: The event triggered when Opensearch changes.
        """
        self.charm.reconcile()

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with Opensearch.

        Args:
            event: The event triggered when the relation is broken.
        """
        self.charm.reconcile()
