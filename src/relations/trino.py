# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Trino relation and ingestion reconciliation."""

import json
import logging
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from charms.trino_k8s.v0.trino_catalog import (
    TrinoCatalogRequirer,  # pylint: disable=E0611
)
from ops import WaitingStatus, framework

import graphql
import literals
from log import log_event_handler

logger = logging.getLogger(__name__)

JUJU_MANAGED_KEY = "JUJU_MANAGED"
INGESTION_NAME_PREFIX = "[juju] "
INGESTION_NAME_SUFFIX = "-ingestion"
GMS_TOKEN_SECRET_NAME = "JUJU_MANAGED_GMS_TOKEN"  # nosec B105
TRINO_PASSWORD_SECRET_PREFIX = "JUJU_MANAGED_TRINO_PASSWORD_"  # nosec B105


@dataclass
class _IngestionParams:
    """Common parameters for Trino ingestion source operations.

    Attributes:
        trino_url: URL of the Trino server.
        username: Trino authentication username.
        password: Trino authentication password.
        access_token: DataHub access token for ingestion sink.
        trino_patterns: Parsed Trino filter patterns dict.
    """

    trino_url: str
    username: str
    password: str
    access_token: str
    trino_patterns: dict


def _build_ingestion_name(catalog_name: str) -> str:
    """Build the canonical ingestion source name for a catalog.

    Args:
        catalog_name: Name of the Trino catalog.

    Returns:
        Formatted ingestion source name.
    """
    return f"{INGESTION_NAME_PREFIX}{catalog_name}{INGESTION_NAME_SUFFIX}"


def _random_daily_schedule() -> str:
    """Generate a random daily cron schedule between 22:00 and 06:00 UTC.

    Returns:
        A cron expression string for a daily run.
    """
    hour = random.choice([22, 23, 0, 1, 2, 3, 4, 5])  # nosec B311
    minute = random.randint(0, 59)  # nosec B311
    return f"{minute} {hour} * * *"


def _compile_extra_env_vars() -> Dict[str, str]:
    """Build the extra_env_vars dict with a Juju-managed marker and dual-case proxy vars.

    Aligns with the proxy normalization in services._compile_standard_proxy_environment.

    Returns:
        Dictionary suitable for JSON-encoding into an extra_env_vars extraArgs value.
    """
    env_vars: Dict[str, str] = {JUJU_MANAGED_KEY: "true"}
    proxy_map = {
        "JUJU_CHARM_HTTP_PROXY": ("HTTP_PROXY", "http_proxy"),
        "JUJU_CHARM_HTTPS_PROXY": ("HTTPS_PROXY", "https_proxy"),
    }

    has_proxy_config = False
    for juju_var, target_vars in proxy_map.items():
        value = os.getenv(juju_var)
        if not value:
            continue
        has_proxy_config = True
        for target in target_vars:
            env_vars[target] = value

    juju_no_proxy = os.getenv("JUJU_CHARM_NO_PROXY")
    if juju_no_proxy:
        has_proxy_config = True

    if has_proxy_config:
        no_proxy_values: List[str] = []
        if juju_no_proxy:
            no_proxy_values.extend(h.strip() for h in juju_no_proxy.split(",") if h.strip())
        if no_proxy_values:
            unique = list(dict.fromkeys(no_proxy_values))
            no_proxy = ",".join(unique)
            env_vars["NO_PROXY"] = no_proxy
            env_vars["no_proxy"] = no_proxy

    return env_vars


def _build_managed_extra_args() -> List[Dict[str, str]]:
    """Build the extraArgs list with a single extra_env_vars entry.

    Returns:
        A list with one StringMapEntry containing JSON-encoded env vars.
    """
    env_vars = _compile_extra_env_vars()
    return [{"key": "extra_env_vars", "value": json.dumps(env_vars)}]


def _extract_patterns(source: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract filter patterns from an existing ingestion source's recipe.

    Args:
        source: Ingestion source dict from the GraphQL response.

    Returns:
        Patterns dict with keys like "schema-pattern", or None if not parseable.
    """
    recipe_str = source.get("config", {}).get("recipe")
    if not recipe_str:
        return None
    try:
        recipe = json.loads(recipe_str)
    except (json.JSONDecodeError, TypeError):
        return None
    config = recipe.get("source", {}).get("config", {})
    key_map = {
        "schema_pattern": "schema-pattern",
        "table_pattern": "table-pattern",
        "view_pattern": "view-pattern",
    }
    patterns = {}
    for recipe_key, pattern_key in key_map.items():
        if recipe_key in config:
            patterns[pattern_key] = config[recipe_key]
    return patterns or None


def _normalize_secret_name(catalog_name: str) -> str:
    """Build a DataHub secret name for a catalog's Trino password.

    Args:
        catalog_name: Trino catalog name.

    Returns:
        Deterministic secret name safe for DataHub.
    """
    normalized = re.sub(r"[^a-zA-Z0-9]", "_", catalog_name).upper()
    return f"{TRINO_PASSWORD_SECRET_PREFIX}{normalized}"


def _is_https(trino_url: str) -> bool:
    """Determine whether a Trino connection uses HTTPS based on the port.

    The catalog interface does not pass the scheme so we determine it
    based on the current conventions.

    Args:
        trino_url: Trino URL in host:port format.

    Returns:
        True if port is 443, False otherwise.
    """
    parts = trino_url.rsplit(":", 1)
    if len(parts) != 2:
        return False
    try:
        return int(parts[1]) == 443
    except ValueError:
        return False


def _build_recipe(
    catalog_name: str,
    params: "_IngestionParams",
    patterns_override: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a DataHub ingestion recipe YAML string for a Trino catalog.

    Credentials are referenced via DataHub-managed secret placeholders
    so that raw values are never stored in the recipe.

    Args:
        catalog_name: Trino catalog name (used as the database field).
        params: Common ingestion parameters.
        patterns_override: If provided, use these patterns instead of params.trino_patterns.

    Returns:
        JSON-encoded recipe string.
    """
    default_pattern = {"allow": [".*"], "deny": []}
    patterns = patterns_override if patterns_override is not None else params.trino_patterns
    source_config = {
        "host_port": params.trino_url,
        "database": catalog_name,
        "username": params.username,
        "schema_pattern": patterns.get("schema-pattern", default_pattern),
        "table_pattern": patterns.get("table-pattern", default_pattern),
        "view_pattern": patterns.get("view-pattern", default_pattern),
        "env": "PROD",
        "stateful_ingestion": {"enabled": True},
    }
    # Password only works over HTTPS; omit it for plain HTTP connections.
    if _is_https(params.trino_url):
        source_config["password"] = f"${{{_normalize_secret_name(catalog_name)}}}"

    recipe = {
        "source": {
            "type": "trino",
            "config": source_config,
        },
        "sink": {
            "type": "datahub-rest",
            "config": {
                "server": f"http://localhost:{literals.GMS_PORT}",
                "token": f"${{{GMS_TOKEN_SECRET_NAME}}}",
            },
        },
    }
    return json.dumps(recipe)


def _filter_juju_managed(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter ingestion sources to those marked as Juju-managed.

    Args:
        sources: List of ingestion source dicts from the GraphQL response.

    Returns:
        Subset of sources that are Juju-managed.
    """
    managed = []
    for source in sources:
        extra_args = source.get("config", {}).get("extraArgs") or []
        for arg in extra_args:
            if arg.get("key") != "extra_env_vars":
                continue
            try:
                env_vars = json.loads(arg.get("value", "{}"))
                if env_vars.get(JUJU_MANAGED_KEY) == "true":
                    managed.append(source)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Malformed extra_env_vars JSON in source '%s'", source.get("name"))
            break
    return managed


def _create_ingestion_source(
    bearer_token: str,
    catalog_name: str,
    params: "_IngestionParams",
) -> str:
    """Create a new ingestion source in DataHub.

    Args:
        bearer_token: Bearer token for authentication.
        catalog_name: Trino catalog name.
        params: Common ingestion parameters.

    Returns:
        URN of the newly created ingestion source.
    """
    recipe = _build_recipe(catalog_name, params)
    extra_args_list = _build_managed_extra_args()

    input_data = {
        "name": _build_ingestion_name(catalog_name),
        "type": "trino",
        "description": f"Juju managed Trino ingestion for {catalog_name}",
        "schedule": {
            "interval": _random_daily_schedule(),
            "timezone": "UTC",
        },
        "config": {
            "recipe": recipe,
            "executorId": literals.DEFAULT_EXECUTOR_ID,
            "extraArgs": extra_args_list,
        },
    }
    return graphql.create_ingestion_source(bearer_token, input_data)


def _update_ingestion_source(
    bearer_token: str,
    urn: str,
    existing_source: Dict[str, Any],
    params: "_IngestionParams",
) -> None:
    """Update credentials and proxy vars of an existing ingestion source.

    Preserves name, type, description, schedule, patterns, and other settings.

    Args:
        bearer_token: Bearer token for authentication.
        urn: URN of the existing ingestion source.
        existing_source: Current source dict from listIngestionSources.
        params: Common ingestion parameters.
    """
    existing_patterns = _extract_patterns(existing_source)
    recipe = _build_recipe(
        _extract_catalog_from_name(existing_source["name"]),
        params,
        patterns_override=existing_patterns,
    )

    # Preserve non-managed extraArgs; replace the managed extra_env_vars block atomically.
    existing_extra = existing_source.get("config", {}).get("extraArgs") or []
    managed_keys = {
        JUJU_MANAGED_KEY,
        "extra_env_vars",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
    preserved = [a for a in existing_extra if a.get("key") not in managed_keys]
    preserved.extend(_build_managed_extra_args())

    existing_schedule = existing_source.get("schedule")
    schedule_input = None
    if existing_schedule:
        schedule_input = {
            "interval": existing_schedule["interval"],
            "timezone": existing_schedule.get("timezone", "UTC"),
        }

    input_data: Dict[str, Any] = {
        "name": existing_source["name"],
        "type": existing_source.get("type", "trino"),
        "config": {
            "recipe": recipe,
            "executorId": existing_source.get("config", {}).get("executorId", literals.DEFAULT_EXECUTOR_ID),
            "extraArgs": preserved,
        },
    }
    if schedule_input:
        input_data["schedule"] = schedule_input

    graphql.update_ingestion_source(bearer_token, urn, input_data)


def _extract_catalog_from_name(name: str) -> str:
    """Extract catalog name from the standard ingestion source name.

    Args:
        name: Ingestion source name of the form '[juju] <catalog>-ingestion'.

    Returns:
        The catalog name portion.
    """
    stripped = name.removeprefix(INGESTION_NAME_PREFIX).removesuffix(INGESTION_NAME_SUFFIX)
    return stripped


class TrinoRelation(framework.Object):
    """Client for datahub:trino-catalog relations.

    Attributes:
        trino_catalog: TrinoCatalogRequirer instance for relation data access.
        access_token: DataHub access token for ingestion source management.
    """

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "trino-catalog")
        self.charm = charm

        self.trino_catalog = TrinoCatalogRequirer(charm, relation_name="trino-catalog")

        charm.framework.observe(
            charm.on.trino_catalog_relation_changed,
            self._on_trino_catalog_changed,
        )
        charm.framework.observe(
            charm.on.trino_catalog_relation_broken,
            self._on_relation_broken,
        )

        self._access_token: Optional[str] = None  # type: ignore[annotation-unchecked]

    def _get_stored_access_token(self) -> Optional[str]:
        """Retrieve the persisted access token from the Juju secret store.

        Returns:
            The stored token string, or None if not yet persisted.
        """
        relation = self.charm.model.get_relation("peer")
        if not relation:
            return None
        secret_id = relation.data[self.charm.app].get(literals.INGESTION_TOKEN_SECRET_LABEL)
        if not secret_id:
            return None
        secret = self.charm.model.get_secret(id=secret_id)
        return secret.get_content(refresh=True).get("token")

    def _store_access_token(self, token: str) -> None:
        """Persist an access token in the Juju secret store.

        Creates the secret on first call; updates the existing secret on subsequent calls.

        Args:
            token: The access token to store.
        """
        relation = self.charm.model.get_relation("peer")
        if not relation:
            return
        content = {"token": token}
        secret_id = relation.data[self.charm.app].get(literals.INGESTION_TOKEN_SECRET_LABEL)
        if secret_id:
            secret = self.charm.model.get_secret(id=secret_id)
            secret.set_content(content)
        else:
            secret = self.charm.app.add_secret(content, label=literals.INGESTION_TOKEN_SECRET_LABEL)
            relation.data[self.charm.app][literals.INGESTION_TOKEN_SECRET_LABEL] = secret.id

    @property
    def access_token(self) -> str:
        """Return (and lazily create) a DataHub access token for ingestion.

        Checks the in-memory cache first, then the Juju secret store,
        and only creates a new token via the DataHub API as a last resort.

        Raises:
            RuntimeError: If token creation fails.
        """
        if self._access_token is not None:
            return self._access_token

        stored = self._get_stored_access_token()
        if stored:
            self._access_token = stored
            return self._access_token

        token = graphql.create_access_token(self.charm.system_client_id, self.charm.system_client_secret)
        self._access_token = token
        self._store_access_token(token)
        return self._access_token

    @log_event_handler(logger)
    def _on_trino_catalog_changed(self, event) -> None:
        """Handle trino-catalog relation changed events.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            return

        self.charm.unit.status = WaitingStatus("handling trino-catalog change")
        self.reconcile_ingestions()
        self.charm._update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with Trino.

        Args:
            event: The event triggered when the relation is broken.
        """
        if not self.charm.unit.is_leader():
            return

        if not self.charm._state.is_ready():
            return

        self._cleanup_managed_ingestions()
        self.charm._update(event)

    def reconcile_ingestions(self) -> None:
        """Reconcile Juju-managed Trino ingestion sources with current relation state."""
        trino_info = self.trino_catalog.get_trino_info()
        if not trino_info:
            logger.info("No Trino info available, skipping reconciliation")
            return

        credentials = self.trino_catalog.get_credentials()
        if not credentials:
            logger.info("No Trino credentials available, skipping reconciliation")
            return

        username, password = credentials
        trino_url = trino_info["trino_url"]
        catalogs = trino_info["trino_catalogs"]
        desired_catalogs = {catalog.name for catalog in catalogs}

        config = self.charm.config
        trino_patterns = json.loads(config.trino_patterns)

        result = self._prepare_ingestion_state(catalogs, password)
        if result is None:
            return
        access_token, managed, secrets_map = result

        existing_by_catalog: Dict[str, Dict[str, Any]] = {}
        for source in managed:
            catalog_name = _extract_catalog_from_name(source["name"])
            existing_by_catalog[catalog_name] = source

        params = _IngestionParams(
            trino_url=trino_url,
            username=username,
            password=password,
            access_token=access_token,
            trino_patterns=trino_patterns,
        )

        self._create_missing_ingestions(desired_catalogs, existing_by_catalog, access_token, params)
        self._update_existing_ingestions(desired_catalogs, existing_by_catalog, access_token, params)
        self._delete_obsolete_ingestions(desired_catalogs, existing_by_catalog, access_token, secrets_map)

    def _prepare_ingestion_state(self, catalogs, password: str) -> Optional[tuple]:
        """Ensure secrets exist and fetch managed ingestion sources, retrying on auth failure.

        Args:
            catalogs: List of Trino catalog objects with a .name attribute.
            password: Current Trino password for per-catalog secrets.

        Returns:
            Tuple of (access_token, managed_sources, secrets_map) or None on failure.
        """
        for attempt in range(2):
            try:
                access_token = self.access_token
                secrets_map = graphql.list_secrets(access_token)
                graphql.ensure_secret(access_token, GMS_TOKEN_SECRET_NAME, access_token, secrets_map)
                for catalog in catalogs:
                    graphql.ensure_secret(access_token, _normalize_secret_name(catalog.name), password, secrets_map)
                all_sources = graphql.list_ingestion_sources(access_token)
                managed = _filter_juju_managed(all_sources)
                return access_token, managed, secrets_map
            except graphql.AuthenticationError:
                if attempt == 0:
                    logger.info("Authentication failed, refreshing token and retrying")
                    self._access_token = None
                    token = graphql.create_access_token(self.charm.system_client_id, self.charm.system_client_secret)
                    self._access_token = token
                    self._store_access_token(token)
                    continue
                logger.error("Authentication failed after token refresh")
            except Exception as e:
                logger.error("Failed to prepare ingestion reconciliation: %s", str(e))
                break
        return None

    def _create_missing_ingestions(
        self,
        desired: Set[str],
        existing: Dict[str, Dict[str, Any]],
        bearer: str,
        params: "_IngestionParams",
    ) -> None:
        """Create ingestion sources for catalogs not yet tracked."""
        for catalog_name in desired - set(existing.keys()):
            try:
                urn = _create_ingestion_source(
                    bearer,
                    catalog_name,
                    params,
                )
                logger.info("Created ingestion source for catalog '%s': %s", catalog_name, urn)
            except Exception as e:
                logger.error("Failed to create ingestion for catalog '%s': %s", catalog_name, str(e))

    def _update_existing_ingestions(
        self,
        desired: Set[str],
        existing: Dict[str, Dict[str, Any]],
        bearer: str,
        params: "_IngestionParams",
    ) -> None:
        """Update ingestion sources whose catalogs are still desired."""
        for catalog_name in desired & set(existing.keys()):
            source = existing[catalog_name]
            try:
                _update_ingestion_source(
                    bearer,
                    source["urn"],
                    source,
                    params,
                )
                logger.info("Updated ingestion source for catalog '%s'", catalog_name)
            except Exception as e:
                logger.error("Failed to update ingestion for catalog '%s': %s", catalog_name, str(e))

    def _delete_obsolete_ingestions(
        self,
        desired: Set[str],
        existing: Dict[str, Dict[str, Any]],
        bearer: str,
        secrets_map: Dict[str, str],
    ) -> None:
        """Delete ingestion sources and their secrets for catalogs no longer in the relation."""
        for catalog_name in set(existing.keys()) - desired:
            source = existing[catalog_name]
            try:
                graphql.delete_ingestion_source(bearer, source["urn"])
                logger.info("Deleted obsolete ingestion source for catalog '%s'", catalog_name)
            except Exception as e:
                logger.error("Failed to delete ingestion for catalog '%s': %s", catalog_name, str(e))
            secret_name = _normalize_secret_name(catalog_name)
            secret_urn = secrets_map.get(secret_name)
            if secret_urn:
                try:
                    graphql.delete_secret(bearer, secret_urn)
                    logger.info("Deleted secret '%s' for catalog '%s'", secret_name, catalog_name)
                except Exception as e:
                    logger.error("Failed to delete secret for catalog '%s': %s", catalog_name, str(e))

    def _cleanup_managed_ingestions(self) -> None:
        """Delete all Juju-managed Trino ingestion sources and secrets (relation-broken cleanup)."""  # noqa W505
        try:
            access_token = self.access_token
            all_sources = graphql.list_ingestion_sources(access_token)
            managed = _filter_juju_managed(all_sources)
            secrets_map = graphql.list_secrets(access_token)
        except Exception as e:
            logger.error("Failed to fetch ingestion sources during cleanup: %s", str(e))
            return

        for source in managed:
            try:
                graphql.delete_ingestion_source(access_token, source["urn"])
                logger.info("Cleaned up ingestion source '%s'", source["name"])
            except Exception as e:
                logger.error("Failed to clean up ingestion '%s': %s", source["name"], str(e))

        for secret_name, secret_urn in secrets_map.items():
            if secret_name == GMS_TOKEN_SECRET_NAME or secret_name.startswith(TRINO_PASSWORD_SECRET_PREFIX):
                try:
                    graphql.delete_secret(access_token, secret_urn)
                    logger.info("Cleaned up secret '%s'", secret_name)
                except Exception as e:
                    logger.error("Failed to clean up secret '%s': %s", secret_name, str(e))
