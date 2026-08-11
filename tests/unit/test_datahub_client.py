# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the datahub-client relation provider."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ops
import pytest
from charms.datahub_k8s.v0.datahub_client import (  # pylint: disable=E0611
    DatahubClientRequirer,
)

import literals
from relations.datahub_client import DatahubClientRelation

SA_URN = "urn:li:corpuser:service_abc"
OTHER_URN = "urn:li:corpuser:service_def"


def _relation(relation_id=7, app_name="datahub-mcp-k8s"):
    """Build a stub ops.Relation good enough for the provider."""
    return SimpleNamespace(
        id=relation_id,
        name=literals.DATAHUB_CLIENT_RELATION_NAME,
        app=SimpleNamespace(name=app_name),
    )


def _client_relation(
    *,
    known_urn=None,
    published_secret_id="secret://existing",
    secret_exists=True,
    ingress_url=None,
):
    """Build a DatahubClientRelation bound to a MagicMock charm without running __init__."""
    charm = MagicMock()
    charm.app.name = "datahub-k8s"
    charm.model.name = "datahub"
    charm.gms_ingress.is_ready.return_value = ingress_url is not None
    charm.gms_ingress.url = ingress_url
    charm.system_client_id = "__datahub_system"
    charm.system_client_secret = "sys-secret"  # nosec B105
    charm.unit.is_leader.return_value = True

    secret = MagicMock()
    secret.id = "secret://minted"
    charm.app.add_secret.return_value = secret
    if secret_exists:
        charm.model.get_secret.return_value = MagicMock(id=published_secret_id)
    else:
        charm.model.get_secret.side_effect = ops.SecretNotFoundError()

    rel = DatahubClientRelation.__new__(DatahubClientRelation)
    rel.charm = charm
    rel.provider = MagicMock()
    rel.provider.get_service_account_urn.return_value = known_urn
    rel.provider.get_secret_id.return_value = published_secret_id
    return rel


CLUSTER_URL = f"http://datahub-k8s.datahub.svc.cluster.local:{literals.GMS_PORT}"


class TestGmsUrl:
    """Tests for the published GMS URL."""

    def test_prefers_the_ingress_url(self):
        """The ingress URL is reachable from other models, the Service name is not."""
        rel = _client_relation(ingress_url="https://gms.example.com/datahub/")
        assert rel.gms_url == "https://gms.example.com/datahub"

    def test_falls_back_to_kubernetes_service_dns(self):
        """Without an ingress, the Juju-created Service for the application is used."""
        rel = _client_relation(ingress_url=None)
        assert rel.gms_url == CLUSTER_URL


class TestEnsureCredentials:
    """Tests for the service account / token provisioning logic."""

    def test_reuses_existing_account_and_secret(self):
        """A live service account with a live secret triggers no DataHub writes."""
        rel = _client_relation(known_urn=SA_URN)
        with (
            patch("graphql.create_service_account") as create_sa,
            patch("graphql.create_access_token") as mint,
        ):
            urn, secret_id = rel._ensure_credentials(_relation(), {SA_URN: "[juju] x-7"})

        assert (urn, secret_id) == (SA_URN, "secret://existing")
        create_sa.assert_not_called()
        mint.assert_not_called()

    def test_creates_account_and_token_when_absent(self):
        """Nothing published yet: create the service account and mint a token."""
        rel = _client_relation(known_urn=None, published_secret_id=None, secret_exists=False)
        with (
            patch("graphql.create_service_account", return_value=SA_URN) as create_sa,
            patch("graphql.create_access_token", return_value="pat") as mint,
        ):
            urn, secret_id = rel._ensure_credentials(_relation(), {})

        assert (urn, secret_id) == (SA_URN, "secret://minted")
        create_sa.assert_called_once()
        assert create_sa.call_args.args[2] == "[juju] datahub-mcp-k8s-7"
        assert mint.call_args.kwargs["actor_urn"] == SA_URN
        rel.charm.app.add_secret.assert_called_once()
        rel.charm.app.add_secret.return_value.grant.assert_called_once()

    def test_recreates_when_service_account_vanished(self):
        """A service account deleted out-of-band is recreated with a fresh token."""
        rel = _client_relation(known_urn=SA_URN)
        with (
            patch("graphql.create_service_account", return_value=OTHER_URN) as create_sa,
            patch("graphql.create_access_token", return_value="pat"),
        ):
            urn, secret_id = rel._ensure_credentials(_relation(), {OTHER_URN: "someone else"})

        assert (urn, secret_id) == (OTHER_URN, "secret://minted")
        create_sa.assert_called_once()
        # The stale secret held a token for a service account that no longer exists.
        rel.charm.model.get_secret.return_value.remove_all_revisions.assert_called_once()

    def test_mints_a_new_token_when_secret_is_missing(self):
        """A live service account whose secret was lost gets a fresh token, same account."""
        rel = _client_relation(known_urn=SA_URN, published_secret_id=None, secret_exists=False)
        with (
            patch("graphql.create_service_account") as create_sa,
            patch("graphql.create_access_token", return_value="pat"),
        ):
            urn, secret_id = rel._ensure_credentials(_relation(), {SA_URN: "[juju] x-7"})

        assert (urn, secret_id) == (SA_URN, "secret://minted")
        create_sa.assert_not_called()

    def test_ignores_a_published_secret_that_no_longer_exists(self):
        """A dangling secret ID must not be handed to the requirer."""
        rel = _client_relation(known_urn=SA_URN, secret_exists=False)
        with patch("graphql.create_access_token", return_value="pat"):
            urn, secret_id = rel._ensure_credentials(_relation(), {SA_URN: "[juju] x-7"})

        assert (urn, secret_id) == (SA_URN, "secret://minted")


class TestReconcileClients:
    """Tests for the reconcile entry point."""

    def test_publishes_connection_for_each_relation(self):
        """Each relation is provisioned and its connection details published."""
        rel = _client_relation(known_urn=SA_URN)
        relation = _relation()
        rel.charm.model.relations = {literals.DATAHUB_CLIENT_RELATION_NAME: [relation]}
        with patch("graphql.list_service_accounts", return_value={SA_URN: "[juju] datahub-mcp-k8s-7"}):
            rel.reconcile_clients()

        rel.provider.publish_connection.assert_called_once_with(relation, CLUSTER_URL, "secret://existing", SA_URN)

    def test_one_failing_relation_does_not_block_the_others(self):
        """A GraphQL failure is logged and the next relation is still provisioned."""
        rel = _client_relation(known_urn=SA_URN)
        rel.charm.model.relations = {literals.DATAHUB_CLIENT_RELATION_NAME: [_relation(1), _relation(2)]}
        with (
            patch("graphql.list_service_accounts", return_value={SA_URN: "[juju] datahub-mcp-k8s-1"}),
            patch("graphql.create_service_account", side_effect=[RuntimeError("gms down"), OTHER_URN]),
            patch("graphql.create_access_token", return_value="pat"),
        ):
            rel.provider.get_service_account_urn.return_value = None
            rel.reconcile_clients()

        assert rel.provider.publish_connection.call_count == 1


class TestDeleteObsoletes:
    """Tests for the obsolete service account sweep."""

    def test_deletes_accounts_whose_relation_is_gone(self):
        """A relation-broken cleanup that failed while GMS was down self-heals."""
        rel = _client_relation()
        accounts = {SA_URN: "[juju] datahub-mcp-k8s-7", OTHER_URN: "[juju] datahub-mcp-k8s-9"}
        with patch("graphql.delete_service_account") as delete_sa:
            rel._delete_obsolete_accounts(accounts, {7})

        delete_sa.assert_called_once_with("__datahub_system", "sys-secret", OTHER_URN)

    def test_leaves_accounts_it_does_not_manage(self):
        """Service accounts a human created must survive reconcile."""
        rel = _client_relation()
        with patch("graphql.delete_service_account") as delete_sa:
            rel._delete_obsolete_accounts({OTHER_URN: "analytics-bot"}, set())

        delete_sa.assert_not_called()


class TestRelationBroken:
    """Tests for relation-broken cleanup."""

    def test_deletes_service_account_and_secret(self):
        """Deleting the service account invalidates every token issued for it."""
        rel = _client_relation(known_urn=SA_URN)
        event = SimpleNamespace(relation=_relation())
        with patch("graphql.delete_service_account") as delete_sa:
            rel._on_relation_broken(event)

        delete_sa.assert_called_once_with("__datahub_system", "sys-secret", SA_URN)
        rel.charm.model.get_secret.assert_called_with(id="secret://existing")
        rel.charm.model.get_secret.return_value.remove_all_revisions.assert_called_once()
        rel.charm.reconcile.assert_called_once()

    def test_non_leader_does_nothing(self):
        """Only the leader owns the DataHub-side resources."""
        rel = _client_relation(known_urn=SA_URN)
        rel.charm.unit.is_leader.return_value = False
        with patch("graphql.delete_service_account") as delete_sa:
            rel._on_relation_broken(SimpleNamespace(relation=_relation()))

        delete_sa.assert_not_called()
        rel.charm.reconcile.assert_not_called()


FULL_DATABAG = {
    "gms-url": "http://gms:8080",
    "secret-id": "secret://x",
    "service-account-urn": SA_URN,
}


def _requirer(databag, token="pat"):  # nosec B107
    """Build a DatahubClientRequirer reading the given provider databag."""
    charm = MagicMock()
    remote_app = MagicMock()
    relation = SimpleNamespace(app=remote_app, data={remote_app: databag})
    charm.model.relations = {"datahub-client": [relation]}
    charm.model.get_secret.return_value.get_content.return_value = {"token": token}

    requirer = DatahubClientRequirer.__new__(DatahubClientRequirer)
    requirer.charm = charm
    requirer.relation_name = "datahub-client"
    return requirer


@pytest.mark.parametrize(
    "databag",
    [
        {},
        {"gms-url": "http://gms:8080"},
        {"gms-url": "http://gms:8080", "secret-id": "secret://x"},
    ],
)
def test_requirer_waits_for_complete_data(databag):
    """The requirer reports no connection until every field is published."""
    assert _requirer(databag).get_connection() is None


def test_requirer_returns_connection_when_complete():
    """A complete databag plus a readable secret yields the connection."""
    connection = _requirer(FULL_DATABAG).get_connection()

    assert connection is not None
    assert connection.gms_url == "http://gms:8080"
    assert connection.token == "pat"  # nosec B105
    assert connection.service_account_urn == SA_URN


def test_requirer_waits_for_a_readable_secret():
    """A granted-but-not-yet-readable secret is not an error, just not ready."""
    requirer = _requirer(FULL_DATABAG)
    requirer.charm.model.get_secret.side_effect = ops.ModelError("permission denied")

    assert requirer.get_connection() is None
