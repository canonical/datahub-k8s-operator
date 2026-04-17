# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for src/relations/trino.py."""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from relations.trino import (
    JUJU_MANAGED_KEY,
    TrinoRelation,
    _build_ingestion_name,
    _build_recipe,
    _compile_proxy_extra_args,
    _extract_catalog_from_name,
    _filter_juju_managed,
    _IngestionParams,
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

    def test_compile_proxy_extra_args_with_proxies(self):
        """Compile proxy args when Juju proxy vars are set."""
        with patch.dict(
            os.environ,
            {
                "JUJU_CHARM_HTTP_PROXY": "http://proxy:8080",
                "JUJU_CHARM_HTTPS_PROXY": "http://proxy:8443",
                "JUJU_CHARM_NO_PROXY": "localhost",
            },
            clear=True,
        ):
            args = _compile_proxy_extra_args()
        assert args["HTTP_PROXY"] == "http://proxy:8080"
        assert args["HTTPS_PROXY"] == "http://proxy:8443"
        assert args["NO_PROXY"] == "localhost"

    def test_compile_proxy_extra_args_without_proxies(self):
        """Compile proxy args when no Juju proxy vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            args = _compile_proxy_extra_args()
        assert not args

    def test_filter_juju_managed_includes_managed(self):
        """Filter includes sources with JUJU_MANAGED=True."""
        sources = [
            {
                "urn": "urn:1",
                "name": "[juju] a-ingestion",
                "config": {"extraArgs": [{"key": JUJU_MANAGED_KEY, "value": "True"}]},
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
        """Build recipe produces valid JSON with expected keys."""
        params = _IngestionParams(
            trino_url="trino:443",
            username="user",
            password="pass",  # nosec
            access_token="tok123",
            schema_pattern='{"allow":[".*"],"deny":[]}',
            table_pattern='{"allow":[".*"],"deny":[]}',
            column_pattern='{"allow":[".*"],"deny":[]}',
        )
        recipe_str = _build_recipe("my_catalog", params)
        recipe = json.loads(recipe_str)
        assert recipe["source"]["type"] == "trino"
        assert recipe["source"]["config"]["database"] == "my_catalog"
        assert recipe["source"]["config"]["host_port"] == "trino:443"
        assert recipe["source"]["config"]["username"] == "user"
        assert recipe["source"]["config"]["stateful_ingestion"]["enabled"] is True
        assert recipe["source"]["config"]["env"] == "PROD"
        assert recipe["sink"]["config"]["token"] == "tok123"


class TestTrinoRelationAuth:
    """Tests for TrinoRelation auth lifecycle properties."""

    def test_access_token_created_lazily(self):
        """Access token is lazily created via _create_access_token."""
        with patch("relations.trino.TrinoCatalogRequirer"):
            charm = MagicMock()
            charm.system_client_id = "__datahub_system"
            charm.system_client_secret = "test-secret"  # nosec B105
            charm.framework = MagicMock()
            charm.on = MagicMock()
            rel = TrinoRelation.__new__(TrinoRelation)
            rel.charm = charm
            rel._access_token = None

        with patch("relations.trino._create_access_token", return_value="tok-abc") as mock_create:
            first = rel.access_token
            second = rel.access_token
            assert first == "tok-abc"
            assert second == "tok-abc"
            mock_create.assert_called_once_with("__datahub_system", "test-secret")


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


class TestReconciliation:
    """Tests for ingestion source reconciliation logic."""

    def _make_relation(self):
        """Create TrinoRelation with mocked charm wired for reconciliation."""
        with patch("relations.trino.TrinoCatalogRequirer"):
            charm = MagicMock()
            charm.unit.is_leader.return_value = True
            charm._state.is_ready.return_value = True
            charm.config = SimpleNamespace(
                schema_pattern='{"allow":[".*"],"deny":[]}',
                table_pattern='{"allow":[".*"],"deny":[]}',
                column_pattern='{"allow":[".*"],"deny":[]}',
            )
            charm.system_client_id = "__datahub_system"
            charm.system_client_secret = "test-secret"  # nosec B105
            charm.framework = MagicMock()
            charm.on = MagicMock()
            rel = TrinoRelation.__new__(TrinoRelation)
            rel.charm = charm
            rel.trino_catalog = MagicMock()
            rel._access_token = "test-token"  # nosec B105
        return rel

    @patch("relations.trino._delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("relations.trino._list_ingestion_sources")
    def test_reconcile_creates_missing(self, mock_list, mock_create, mock_update, mock_delete):
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

        rel._reconcile_ingestions()

        mock_create.assert_called_once()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @patch("relations.trino._delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("relations.trino._list_ingestion_sources")
    def test_reconcile_updates_existing(self, mock_list, mock_create, mock_update, mock_delete):
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
                "config": {"extraArgs": [{"key": JUJU_MANAGED_KEY, "value": "True"}]},
                "schedule": {"interval": "30 3 * * *", "timezone": "UTC"},
            }
        ]

        rel._reconcile_ingestions()

        mock_create.assert_not_called()
        mock_update.assert_called_once()
        mock_delete.assert_not_called()

    @patch("relations.trino._delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("relations.trino._list_ingestion_sources")
    def test_reconcile_deletes_obsolete(self, mock_list, mock_create, mock_update, mock_delete):
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
                "config": {"extraArgs": [{"key": JUJU_MANAGED_KEY, "value": "True"}]},
            }
        ]

        rel._reconcile_ingestions()

        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_called_once_with("test-token", "urn:old")

    @patch("relations.trino._delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("relations.trino._list_ingestion_sources")
    def test_reconcile_noop_when_synced(self, mock_list, mock_create, mock_update, mock_delete):
        """Reconcile makes no changes when state is already in sync (no-op)."""
        rel = self._make_relation()
        rel.trino_catalog.get_trino_info.return_value = None

        rel._reconcile_ingestions()

        mock_list.assert_not_called()
        mock_create.assert_not_called()
        mock_update.assert_not_called()
        mock_delete.assert_not_called()

    @patch("relations.trino._delete_ingestion_source")
    @patch("relations.trino._list_ingestion_sources")
    def test_cleanup_deletes_all_managed(self, mock_list, mock_delete):
        """Cleanup on relation-broken deletes all Juju-managed sources."""
        rel = self._make_relation()
        mock_list.return_value = [
            {
                "urn": "urn:a",
                "name": "[juju] a-ingestion",
                "config": {"extraArgs": [{"key": JUJU_MANAGED_KEY, "value": "True"}]},
            },
            {
                "urn": "urn:b",
                "name": "manual-source",
                "config": {"extraArgs": []},
            },
        ]

        rel._cleanup_managed_ingestions()

        mock_delete.assert_called_once_with("test-token", "urn:a")


class TestScheduleStability:
    """Tests for schedule-once policy."""

    @patch("relations.trino._delete_ingestion_source")
    @patch("relations.trino._update_ingestion_source")
    @patch("relations.trino._create_ingestion_source")
    @patch("relations.trino._list_ingestion_sources")
    def test_update_preserves_existing_schedule(self, mock_list, mock_create, mock_update, mock_delete):
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
                    "extraArgs": [{"key": JUJU_MANAGED_KEY, "value": "True"}],
                    "executorId": "default",
                },
                "schedule": original_schedule,
            }
        ]

        rel._reconcile_ingestions()

        call_args = mock_update.call_args
        passed_source = call_args[0][2]
        assert passed_source["schedule"] == original_schedule
