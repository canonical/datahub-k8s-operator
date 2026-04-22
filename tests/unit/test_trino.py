# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for src/relations/trino.py."""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import literals
from graphql import AuthenticationError
from relations.trino import (
    GMS_TOKEN_SECRET_NAME,
    JUJU_MANAGED_KEY,
    TrinoRelation,
    _build_ingestion_name,
    _build_managed_extra_args,
    _build_recipe,
    _compile_extra_env_vars,
    _extract_catalog_from_name,
    _filter_juju_managed,
    _IngestionParams,
    _normalize_secret_name,
    _random_daily_schedule,
)


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_build_ingestion_name(self):
        """Build canonical name from catalog name."""
        assert _build_ingestion_name("sales") == "[juju] sales-ingestion"

    def test_extract_catalog_from_name(self):
        """Extract catalog name from canonical ingestion name."""
        assert _extract_catalog_from_name("[juju] sales-ingestion") == "sales"

    def test_extract_catalog_from_name_plain(self):
        """Extract from a name without the prefix/suffix gracefully."""
        assert _extract_catalog_from_name("sales") == "sales"

    def test_random_daily_schedule_format(self):
        """Schedule string is a valid 5-field cron within the expected hours."""
        schedule = _random_daily_schedule()
        parts = schedule.split()
        assert len(parts) == 5
        minute = int(parts[0])
        hour = int(parts[1])
        assert 0 <= minute <= 59
        assert hour in {22, 23, 0, 1, 2, 3, 4, 5}

    def test_compile_extra_env_vars_with_proxies(self):
        """Compile extra_env_vars when Juju proxy vars are set."""
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy:8080",
                "JUJU_CHARM_HTTPS_PROXY": "http://proxy:8443",
                "JUJU_CHARM_NO_PROXY": "localhost",
            },
            clear=True,
        ):
            env = _compile_extra_env_vars()
        assert env[JUJU_MANAGED_KEY] == "true"
        assert env["HTTP_PROXY"] == "http://proxy:8080"
        assert env["http_proxy"] == "http://proxy:8080"
        assert env["HTTPS_PROXY"] == "http://proxy:8443"
        assert env["https_proxy"] == "http://proxy:8443"
        assert env["NO_PROXY"] == "localhost"
        assert env["no_proxy"] == "localhost"

    def test_compile_extra_env_vars_without_proxies(self):
        """Compile extra_env_vars when no Juju proxy vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            env = _compile_extra_env_vars()
        assert env == {JUJU_MANAGED_KEY: "true"}

    def test_build_managed_extra_args_structure(self):
        """Build managed extra_args produces a single extra_env_vars entry."""
        with patch.dict(os.environ, {}, clear=True):
            args = _build_managed_extra_args()
        assert len(args) == 1
        assert args[0]["key"] == "extra_env_vars"
        env = json.loads(args[0]["value"])
        assert env[JUJU_MANAGED_KEY] == "true"

    def test_normalize_secret_name(self):
        """Normalize catalog name into a valid DataHub secret name."""
        assert _normalize_secret_name("my-catalog.test") == "JUJU_MANAGED_TRINO_PASSWORD_MY_CATALOG_TEST"

    def test_filter_juju_managed_extra_env_vars_format(self):
        """Filter detects the extra_env_vars JSON format."""
        sources = [
            {
                "urn": "urn:1",
                "name": "[juju] a-ingestion",
                "config": {
                    "extraArgs": [
                        {
                            "key": "extra_env_vars",
                            "value": json.dumps({JUJU_MANAGED_KEY: "true"}),
                        }
                    ]
                },
            },
            {
                "urn": "urn:2",
                "name": "manual-source",
                "config": {"extraArgs": []},
            },
        ]
        result = _filter_juju_managed(sources)
        assert len(result) == 1
        assert result[0]["urn"] == "urn:1"

    def test_filter_juju_managed_empty_extra_args(self):
        """Filter handles sources with no extraArgs gracefully."""
        sources = [
            {"urn": "urn:1", "name": "foo", "config": {}},
            {"urn": "urn:2", "name": "bar", "config": {"extraArgs": None}},
        ]
        result = _filter_juju_managed(sources)
        assert not result

    def test_build_recipe_structure(self):
        """Build recipe produces valid JSON with expected keys and secret references."""
        default_pattern = {"allow": [".*"], "deny": []}
        params = _IngestionParams(
            trino_url="trino:443",
            username="user",
            password="pass",  # nosec
            access_token="tok123",
            trino_patterns={
                "schema-pattern": default_pattern,
                "table-pattern": default_pattern,
                "view-pattern": default_pattern,
            },
        )
        recipe_str = _build_recipe("my_catalog", params)
        recipe = json.loads(recipe_str)
        assert recipe["source"]["type"] == "trino"
        assert recipe["source"]["config"]["database"] == "my_catalog"
        assert recipe["source"]["config"]["host_port"] == "trino:443"
        assert recipe["source"]["config"]["username"] == "user"
        assert recipe["source"]["config"]["view_pattern"] == default_pattern
        assert "column_pattern" not in recipe["source"]["config"]
        assert recipe["source"]["config"]["stateful_ingestion"]["enabled"] is True
        assert recipe["source"]["config"]["env"] == "PROD"
        # Credentials use DataHub secret references
        assert recipe["source"]["config"]["password"] == "${JUJU_MANAGED_TRINO_PASSWORD_MY_CATALOG}"
        assert recipe["sink"]["config"]["token"] == f"${{{GMS_TOKEN_SECRET_NAME}}}"


class TestTrinoRelationAuth:
    """Tests for TrinoRelation auth lifecycle properties."""

    def _make_auth_relation(self):
        """Create TrinoRelation with mocked charm for auth tests."""
        with patch("relations.trino.TrinoCatalogRequirer"):
            charm = MagicMock()
            charm.system_client_id = literals.SYSTEM_CLIENT_ID
            charm.system_client_secret = "test-secret"  # nosec B105
            charm.framework = MagicMock()
            charm.on = MagicMock()
            rel = TrinoRelation.__new__(TrinoRelation)
            rel.charm = charm
            rel._access_token = None
        return rel

    def test_access_token_created_when_no_stored(self):
        """Access token is created and persisted when nothing is stored."""
        rel = self._make_auth_relation()
        with (
            patch.object(rel, "_get_stored_access_token", return_value=None),
            patch.object(rel, "_store_access_token") as mock_store,
            patch("graphql.create_access_token", return_value="tok-abc") as mock_create,
        ):
            first = rel.access_token
            second = rel.access_token
            assert first == "tok-abc"
            assert second == "tok-abc"
            mock_create.assert_called_once_with(literals.SYSTEM_CLIENT_ID, "test-secret")
            mock_store.assert_called_once_with("tok-abc")

    def test_access_token_reused_from_store(self):
        """Stored token is reused without calling _create_access_token."""
        rel = self._make_auth_relation()
        with (
            patch.object(rel, "_get_stored_access_token", return_value="stored-tok"),
            patch("graphql.create_access_token") as mock_create,
        ):
            assert rel.access_token == "stored-tok"  # nosec B105
            mock_create.assert_not_called()


class TestTrinoRelationEvents:
    """Tests for TrinoRelation event handlers."""

    def _make_relation(self):
        """Create TrinoRelation with mocked charm and library."""
        with patch("relations.trino.TrinoCatalogRequirer"):
            charm = MagicMock()
            charm.unit.is_leader.return_value = True
            charm._state.is_ready.return_value = True
            charm.framework = MagicMock()
            charm.on = MagicMock()
            rel = TrinoRelation.__new__(TrinoRelation)
            rel.charm = charm
            rel.trino_catalog = MagicMock()
            rel._access_token = None
        return rel

    def test_on_changed_skips_non_leader(self):
        """Changed event is skipped when unit is not leader."""
        rel = self._make_relation()
        rel.charm.unit.is_leader.return_value = False
        event = MagicMock()

        rel._on_trino_catalog_changed(event)

        rel.charm._update.assert_not_called()

    def test_on_changed_defers_when_state_not_ready(self):
        """Changed event is deferred when peer relation is not ready."""
        rel = self._make_relation()
        rel.charm._state.is_ready.return_value = False
        event = MagicMock()

        rel._on_trino_catalog_changed(event)

        event.defer.assert_called_once()

    def test_on_broken_skips_non_leader(self):
        """Broken event is skipped when unit is not leader."""
        rel = self._make_relation()
        rel.charm.unit.is_leader.return_value = False
        event = MagicMock()

        rel._on_relation_broken(event)

        rel.charm._update.assert_not_called()

    def test_on_broken_defers_when_state_not_ready(self):
        """Broken event is deferred when peer relation is not ready."""
        rel = self._make_relation()
        rel.charm._state.is_ready.return_value = False
        event = MagicMock()

        rel._on_relation_broken(event)

        event.defer.assert_called_once()


class TestReconciliation:  # pylint: disable=too-many-positional-arguments
    """Tests for ingestion source reconciliation logic."""

    def _make_relation(self):
        """Create TrinoRelation with mocked charm wired for reconciliation."""
        default_pattern = {"allow": [".*"], "deny": []}
        trino_patterns = json.dumps(
            {
                "schema-pattern": default_pattern,
                "table-pattern": default_pattern,
                "view-pattern": default_pattern,
            }
        )
        with patch("relations.trino.TrinoCatalogRequirer"):
            charm = MagicMock()
            charm.unit.is_leader.return_value = True
            charm._state.is_ready.return_value = True
            charm.config = SimpleNamespace(trino_patterns=trino_patterns)
            charm.system_client_id = literals.SYSTEM_CLIENT_ID
            charm.system_client_secret = "test-secret"  # nosec B105
            charm.framework = MagicMock()
            charm.on = MagicMock()
            rel = TrinoRelation.__new__(TrinoRelation)
            rel.charm = charm
            rel.trino_catalog = MagicMock()
            rel._access_token = "test-token"  # nosec B105
        return rel

    @patch("graphql.delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    @patch("graphql.ensure_secret")
    @patch("graphql.list_secrets", return_value={})
    def test_reconcile_creates_missing(self, _mock_ls, _mock_es, mock_list, mock_create, mock_update, mock_delete):
        """Reconcile creates ingestion for catalogs not yet present."""
        rel = self._make_relation()
        catalog = SimpleNamespace(name="sales")
        rel.trino_catalog.get_trino_info.return_value = {
            "trino_url": "trino:443",
            "trino_catalogs": [catalog],
        }
        rel.trino_catalog.get_credentials.return_value = ("user", "pass")
        mock_list.return_value = []
        mock_create.return_value = "urn:new"

        rel.reconcile_ingestions()

        mock_create.assert_called_once()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @patch("graphql.delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    @patch("graphql.ensure_secret")
    @patch("graphql.list_secrets", return_value={})
    def test_reconcile_updates_existing(self, _mock_ls, _mock_es, mock_list, mock_create, mock_update, mock_delete):
        """Reconcile updates ingestion for catalogs already present."""
        rel = self._make_relation()
        catalog = SimpleNamespace(name="sales")
        rel.trino_catalog.get_trino_info.return_value = {
            "trino_url": "trino:443",
            "trino_catalogs": [catalog],
        }
        rel.trino_catalog.get_credentials.return_value = ("user", "pass")
        mock_list.return_value = [
            {
                "urn": "urn:existing",
                "name": "[juju] sales-ingestion",
                "type": "trino",
                "config": {"extraArgs": [{"key": "extra_env_vars", "value": json.dumps({JUJU_MANAGED_KEY: "true"})}]},
                "schedule": {"interval": "30 3 * * *", "timezone": "UTC"},
            }
        ]

        rel.reconcile_ingestions()

        mock_create.assert_not_called()
        mock_update.assert_called_once()
        mock_delete.assert_not_called()

    @patch("graphql.delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    @patch("graphql.ensure_secret")
    @patch("graphql.list_secrets", return_value={})
    def test_reconcile_deletes_obsolete(self, _mock_ls, _mock_es, mock_list, mock_create, mock_update, mock_delete):
        """Reconcile deletes ingestion for catalogs no longer in relation."""
        rel = self._make_relation()
        rel.trino_catalog.get_trino_info.return_value = {
            "trino_url": "trino:443",
            "trino_catalogs": [],
        }
        rel.trino_catalog.get_credentials.return_value = ("user", "pass")
        mock_list.return_value = [
            {
                "urn": "urn:old",
                "name": "[juju] legacy-ingestion",
                "type": "trino",
                "config": {"extraArgs": [{"key": "extra_env_vars", "value": json.dumps({JUJU_MANAGED_KEY: "true"})}]},
            }
        ]

        rel.reconcile_ingestions()

        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_called_once_with("test-token", "urn:old")

    @patch("graphql.delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    @patch("graphql.ensure_secret")
    @patch("graphql.list_secrets", return_value={})
    def test_reconcile_noop_when_synced(self, _mock_ls, _mock_es, mock_list, mock_create, mock_update, mock_delete):
        """Reconcile makes no changes when state is already in sync (no-op)."""
        rel = self._make_relation()
        rel.trino_catalog.get_trino_info.return_value = None

        rel.reconcile_ingestions()

        mock_list.assert_not_called()
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @patch("graphql.delete_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    def test_cleanup_deletes_all_managed(self, mock_list, mock_delete):
        """Cleanup on relation-broken deletes all Juju-managed sources."""
        rel = self._make_relation()
        mock_list.return_value = [
            {
                "urn": "urn:a",
                "name": "[juju] a-ingestion",
                "config": {
                    "extraArgs": [
                        {
                            "key": "extra_env_vars",
                            "value": json.dumps({JUJU_MANAGED_KEY: "true"}),
                        }
                    ]
                },
            },
            {
                "urn": "urn:b",
                "name": "manual-source",
                "config": {"extraArgs": []},
            },
        ]

        rel._cleanup_managed_ingestions()

        mock_delete.assert_called_once_with("test-token", "urn:a")


class TestScheduleStability:  # pylint: disable=too-many-positional-arguments
    """Tests for schedule-once policy."""

    @patch("graphql.delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    @patch("graphql.ensure_secret")
    @patch("graphql.list_secrets", return_value={})
    def test_update_preserves_existing_schedule(
        self, _mock_ls, _mock_es, mock_list, mock_create, mock_update, mock_delete
    ):
        """Update reconciliation preserves the original schedule."""
        rel = TestReconciliation()._make_relation()
        catalog = SimpleNamespace(name="marketing")
        rel.trino_catalog.get_trino_info.return_value = {
            "trino_url": "trino:443",
            "trino_catalogs": [catalog],
        }
        rel.trino_catalog.get_credentials.return_value = ("user", "pass")
        original_schedule = {"interval": "15 23 * * *", "timezone": "UTC"}
        mock_list.return_value = [
            {
                "urn": "urn:marketing",
                "name": "[juju] marketing-ingestion",
                "type": "trino",
                "config": {
                    "extraArgs": [{"key": "extra_env_vars", "value": json.dumps({JUJU_MANAGED_KEY: "true"})}],
                    "executorId": literals.DEFAULT_EXECUTOR_ID,
                },
                "schedule": original_schedule,
            }
        ]

        rel.reconcile_ingestions()

        call_args = mock_update.call_args
        passed_source = call_args[0][2]
        assert passed_source["schedule"] == original_schedule


class TestAuthRetry:  # pylint: disable=too-many-positional-arguments
    """Tests for authentication error handling and token refresh."""

    def _make_relation(self):
        """Create TrinoRelation wired for auth retry tests."""
        return TestReconciliation()._make_relation()

    @patch("graphql.delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("graphql.list_ingestion_sources")
    @patch("graphql.ensure_secret")
    @patch("graphql.list_secrets")
    def test_reconcile_retries_on_auth_error(
        self, mock_ls, _mock_es, mock_list, mock_create, _mock_update, _mock_delete
    ):
        """Reconcile creates a fresh token, persists it, and retries on auth failure."""
        rel = self._make_relation()
        catalog = SimpleNamespace(name="sales")
        rel.trino_catalog.get_trino_info.return_value = {
            "trino_url": "trino:443",
            "trino_catalogs": [catalog],
        }
        rel.trino_catalog.get_credentials.return_value = ("user", "pass")
        mock_ls.side_effect = [AuthenticationError("expired"), {}]
        mock_list.return_value = []
        mock_create.return_value = "urn:new"

        with (
            patch("graphql.create_access_token", return_value="new-tok"),
            patch.object(rel, "_store_access_token") as mock_store,
        ):
            rel.reconcile_ingestions()

        assert mock_ls.call_count == 2
        mock_create.assert_called_once()
        mock_store.assert_called_once_with("new-tok")
