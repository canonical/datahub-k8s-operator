# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the DataHub-client relation, provisioning per-relation service accounts."""

import logging
import re
from typing import Dict, Optional, Set, Tuple

import ops
from charms.datahub_k8s.v0.datahub_client import (  # pylint: disable=E0611
    TOKEN_SECRET_KEY,
    DatahubClientProvider,
)
from ops import framework

import graphql
import literals

logger = logging.getLogger(__name__)

# Service accounts this charm manages are named "[juju] <app>-<relation id>",
# e.g. "[juju] datahub-mcp-k8s-7". The prefix marks them as ours so accounts a
# human created are never touched; the trailing ID says which relation an
# account belongs to, which is what lets reconcile spot the obsoletes. An app
# name may itself contain digits and hyphens, so only the last `-<digits>`
# is the relation ID.
_MANAGED_NAME_PATTERN = re.compile(rf"^{re.escape(literals.DATAHUB_CLIENT_SA_NAME_PREFIX)}.+-(\d+)$")


def _relation_id_from_name(display_name: str) -> Optional[int]:
    """Return the relation ID encoded in a managed service account's name.

    Args:
        display_name: DataHub display name of the service account.

    Returns:
        The relation ID, or None when the account is not Juju-managed.
    """
    match = _MANAGED_NAME_PATTERN.match(display_name or "")
    return int(match.group(1)) if match else None


class DatahubClientRelation(framework.Object):
    """Client for datahub:datahub-client relations.

    Each relation gets its own DataHub service account and its own access
    token. The token lives in an app-owned Juju secret granted to the relation,
    so it is never written into a databag.

    The service account holds no write privileges: it only inherits DataHub's
    default all-users read policies, which is the least privilege a read-only
    metadata consumer needs.

    Attributes:
        charm: The charm this relation is attached to.
        provider: The DatahubClientProvider handling the relation databag.
        gms_url: URL of the GMS API published to requirers.
    """

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, literals.DATAHUB_CLIENT_RELATION_NAME)
        self.charm = charm
        self.provider = DatahubClientProvider(charm, relation_name=literals.DATAHUB_CLIENT_RELATION_NAME)

        charm.framework.observe(
            charm.on[literals.DATAHUB_CLIENT_RELATION_NAME].relation_changed,
            self._on_relation_changed,
        )
        charm.framework.observe(
            charm.on[literals.DATAHUB_CLIENT_RELATION_NAME].relation_broken,
            self._on_relation_broken,
        )

    @property
    def gms_url(self) -> str:
        """Return the URL of the GMS API to publish to requirers."""
        ingress_url = self.charm.gms_ingress.url if self.charm.gms_ingress.is_ready() else None
        if ingress_url:
            return ingress_url.rstrip("/")
        return f"http://{self.charm.app.name}.{self.charm.model.name}.svc.cluster.local:{literals.GMS_PORT}"

    def _on_relation_changed(self, event) -> None:
        """Handle datahub-client relation changed events.

        Args:
            event: The event triggered when the relation changed.
        """
        self.charm.reconcile()

    def _on_relation_broken(self, event) -> None:
        """Delete the DataHub service account backing a departing relation.

        Args:
            event: The event triggered when the relation is broken.
        """
        if not self.charm.unit.is_leader():
            return

        urn = self.provider.get_service_account_urn(event.relation)
        if urn:
            try:
                graphql.delete_service_account(self.charm.system_client_id, self.charm.system_client_secret, urn)
                logger.info("Deleted DataHub service account '%s'", urn)
            except Exception as e:  # pylint: disable=W0703
                logger.error("Failed to delete DataHub service account '%s': %s", urn, str(e))

        self._remove_secret(event.relation)
        self.charm.reconcile()

    def reconcile_clients(self) -> None:
        """Ensure every datahub-client relation has a service account and a token."""
        relations = self.charm.model.relations.get(literals.DATAHUB_CLIENT_RELATION_NAME, [])
        existing = graphql.list_service_accounts(self.charm.system_client_id, self.charm.system_client_secret)

        for relation in relations:
            try:
                urn, secret_id = self._ensure_credentials(relation, existing)
            except Exception as e:  # pylint: disable=W0703
                logger.error("Failed to provision datahub-client relation %s: %s", relation.id, str(e))
                continue
            self.provider.publish_connection(relation, self.gms_url, secret_id, urn)

        self._delete_obsolete_accounts(existing, {relation.id for relation in relations})

    def _delete_obsolete_accounts(self, existing: Dict[str, str], live_relation_ids: Set[int]) -> None:
        """Delete service accounts left behind by relations that no longer exist.

        `relation-broken` deletes eagerly, but it is a charm's last chance: if
        GMS happens to be unreachable then, the account survives its relation.
        Reconcile sweeps those up on the next pass.

        Args:
            existing: URN-to-display-name mapping of DataHub service accounts.
            live_relation_ids: IDs of the relations that currently exist.
        """
        for urn, display_name in existing.items():
            relation_id = _relation_id_from_name(display_name)
            if relation_id is None or relation_id in live_relation_ids:
                continue
            try:
                graphql.delete_service_account(self.charm.system_client_id, self.charm.system_client_secret, urn)
                logger.info("Deleted obsolete DataHub service account '%s' (%s)", display_name, urn)
            except Exception as e:  # pylint: disable=W0703
                logger.error("Failed to delete obsolete service account '%s': %s", urn, str(e))

    def _ensure_credentials(self, relation: ops.Relation, existing: Dict[str, str]) -> Tuple[str, str]:
        """Return the service account URN and token secret ID for a relation.

        Both are created on first call. The service account is checked against
        DataHub rather than trusted from the databag, so an account deleted
        out-of-band (or lost with a restored backend) is recreated together
        with a fresh token.

        Args:
            relation: The relation to provision.
            existing: URN-to-display-name mapping of DataHub service accounts.

        Returns:
            Tuple of (service account URN, Juju secret ID).
        """
        known_urn = self.provider.get_service_account_urn(relation)

        if known_urn and known_urn in existing:
            secret_id = self._get_secret_id(relation)
            if secret_id:
                return known_urn, secret_id
            logger.info("Token secret for relation %s is missing, minting a new token", relation.id)
            return known_urn, self._create_secret(relation, known_urn)

        urn = graphql.create_service_account(
            self.charm.system_client_id,
            self.charm.system_client_secret,
            self._service_account_name(relation),
            f"Read-only DataHub service account managed by Juju for the "
            f"'{relation.name}' relation with '{relation.app.name if relation.app else 'unknown'}'.",
        )
        logger.info("Created DataHub service account '%s' for relation %s", urn, relation.id)
        # The previous secret, if any, holds a token for a service account that
        # no longer exists.
        self._remove_secret(relation)
        return urn, self._create_secret(relation, urn)

    def _service_account_name(self, relation: ops.Relation) -> str:
        """Return the DataHub display name for a relation's service account.

        Args:
            relation: The relation the service account belongs to.

        Returns:
            A display name that is unique per relation.
        """
        app_name = relation.app.name if relation.app else literals.DATAHUB_CLIENT_RELATION_NAME
        return f"{literals.DATAHUB_CLIENT_SA_NAME_PREFIX}{app_name}-{relation.id}"

    def _get_secret_id(self, relation: ops.Relation) -> Optional[str]:
        """Return the ID of a relation's live token secret, or None if absent.

        Args:
            relation: The relation to read.

        Returns:
            The Juju secret ID, or None when nothing usable is published.
        """
        secret_id = self.provider.get_secret_id(relation)
        if not secret_id:
            return None
        try:
            self.charm.model.get_secret(id=secret_id)
        except ops.SecretNotFoundError:
            return None
        return secret_id

    def _create_secret(self, relation: ops.Relation, service_account_urn: str) -> str:
        """Mint an access token and store it in a Juju secret granted to a relation.

        Args:
            relation: The relation to grant the secret to.
            service_account_urn: URN the token authenticates as.

        Returns:
            The Juju secret ID.
        """
        token = graphql.create_access_token(
            self.charm.system_client_id,
            self.charm.system_client_secret,
            actor_urn=service_account_urn,
            name=self._service_account_name(relation),
        )
        secret = self.charm.app.add_secret({TOKEN_SECRET_KEY: token})
        secret.grant(relation)
        logger.info("Created token secret for relation %s", relation.id)
        return secret.id

    def _remove_secret(self, relation: ops.Relation) -> None:
        """Remove a relation's token secret if it exists.

        Args:
            relation: The relation whose secret should be removed.
        """
        secret_id = self.provider.get_secret_id(relation)
        if not secret_id:
            return
        try:
            self.charm.model.get_secret(id=secret_id).remove_all_revisions()
        except ops.SecretNotFoundError:
            logger.debug("No token secret to remove for relation %s", relation.id)
