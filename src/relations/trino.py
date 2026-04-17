# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define DataHub-Trino relation and ingestion reconciliation."""

import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import requests
from charms.trino_k8s.v0.trino_catalog import (
    TrinoCatalogRequirer,  # pylint: disable=E0611
)
from ops import WaitingStatus, framework

import literals
from log import log_event_handler

logger = logging.getLogger(__name__)

GRAPHQL_ENDPOINT = f"http://localhost:{literals.GMS_PORT}/api/graphql"
JUJU_MANAGED_KEY = "JUJU_MANAGED"
INGESTION_NAME_PREFIX = "[juju] "
INGESTION_NAME_SUFFIX = "-ingestion"


@dataclass
class _IngestionParams:
    """Common parameters for Trino ingestion source operations.

    Attributes:
        trino_url: URL of the Trino server.
        username: Trino authentication username.
        password: Trino authentication password.
        access_token: DataHub access token for ingestion sink.
        schema_pattern: JSON pattern for schema filtering.
        table_pattern: JSON pattern for table filtering.
        column_pattern: JSON pattern for column filtering.
    """

    trino_url: str
    username: str
    password: str
    access_token: str
    schema_pattern: str
    table_pattern: str
    column_pattern: str


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


def _compile_proxy_extra_args() -> Dict[str, str]:
    """Compile proxy-related extra args from Juju model proxy environment.

    Returns:
        Dictionary of proxy environment variable key-value pairs.
    """
    args: Dict[str, str] = {}
    proxy_map = {
        "JUJU_CHARM_HTTP_PROXY": "HTTP_PROXY",
        "JUJU_CHARM_HTTPS_PROXY": "HTTPS_PROXY",
        "JUJU_CHARM_NO_PROXY": "NO_PROXY",
    }
    for juju_var, target_var in proxy_map.items():
        value = os.getenv(juju_var)
        if value:
            args[target_var] = value
    return args


def _build_recipe(catalog_name: str, params: "_IngestionParams") -> str:
    """Build a DataHub ingestion recipe YAML string for a Trino catalog.

    Args:
        catalog_name: Trino catalog name (used as the database field).
        params: Common ingestion parameters.

    Returns:
        JSON-encoded recipe string.
    """
    recipe = {
        "source": {
            "type": "trino",
            "config": {
                "host_port": params.trino_url,
                "database": catalog_name,
                "username": params.username,
                "password": params.password,
                "schema_pattern": json.loads(params.schema_pattern),
                "table_pattern": json.loads(params.table_pattern),
                "column_pattern": json.loads(params.column_pattern),
                "env": "PROD",
                "stateful_ingestion": {"enabled": True},
            },
        },
        "sink": {
            "type": "datahub-rest",
            "config": {
                "server": f"http://localhost:{literals.GMS_PORT}",
                "token": params.access_token,
            },
        },
    }
    return json.dumps(recipe)


def _graphql_request(
    query: str,
    variables: Optional[Dict[str, Any]],
    token: str,
    auth_scheme: str = "Bearer",
) -> Dict[str, Any]:
    """Execute a GraphQL request against the DataHub GMS endpoint.

    Args:
        query: GraphQL query or mutation string.
        variables: Optional variables for the query.
        token: Authentication token or credentials.
        auth_scheme: HTTP auth scheme (e.g. "Bearer" or "Basic").

    Returns:
        The parsed JSON response body.

    Raises:
        RuntimeError: If the request fails or returns GraphQL errors.
    """
    headers = {
        "Authorization": f"{auth_scheme} {token}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    response = requests.post(GRAPHQL_ENDPOINT, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    body = response.json()

    if "errors" in body and body["errors"]:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")

    return body


def _create_access_token(system_client_id: str, system_client_secret: str) -> str:
    """Create a DataHub access token for the system user.

    Uses Basic auth with system client credentials to bootstrap a token.

    Args:
        system_client_id: DataHub system client identifier.
        system_client_secret: DataHub system client secret.

    Returns:
        The generated access token string.
    """
    query = """
    mutation createAccessToken($input: CreateAccessTokenInput!) {
        createAccessToken(input: $input) {
            accessToken
        }
    }
    """
    variables = {
        "input": {
            "type": "PERSONAL",
            "actorUrn": "urn:li:corpuser:__datahub_system",
            "duration": "NO_EXPIRY",
            "name": "juju-managed-ingestion-token",
        }
    }
    basic_credentials = f"{system_client_id}:{system_client_secret}"
    body = _graphql_request(query, variables, basic_credentials, auth_scheme="Basic")
    return body["data"]["createAccessToken"]["accessToken"]


def _list_ingestion_sources(bearer_token: str) -> List[Dict[str, Any]]:
    """Fetch all ingestion sources from DataHub.

    Args:
        bearer_token: Bearer token for authentication.

    Returns:
        List of ingestion source dictionaries.
    """
    query = """
    query listIngestionSources($input: ListIngestionSourcesInput!) {
        listIngestionSources(input: $input) {
            total
            ingestionSources {
                urn
                name
                type
                config {
                    recipe
                    executorId
                    extraArgs {
                        key
                        value
                    }
                }
                schedule {
                    interval
                    timezone
                }
            }
        }
    }
    """
    start = 0
    count = 100
    all_sources: List[Dict[str, Any]] = []

    while True:
        variables = {"input": {"start": start, "count": count}}
        body = _graphql_request(query, variables, bearer_token)
        data = body["data"]["listIngestionSources"]
        sources = data.get("ingestionSources", [])
        all_sources.extend(sources)
        if len(all_sources) >= data["total"]:
            break
        start += count

    return all_sources


def _filter_juju_managed(sources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter ingestion sources to those marked as Juju-managed.

    Args:
        sources: List of ingestion source dicts from the GraphQL response.

    Returns:
        Subset of sources that have JUJU_MANAGED=True in extraArgs.
    """
    managed = []
    for source in sources:
        extra_args = source.get("config", {}).get("extraArgs") or []
        for arg in extra_args:
            if arg.get("key") == JUJU_MANAGED_KEY and arg.get("value") == "True":
                managed.append(source)
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
    query = """
    mutation createIngestionSource($input: UpdateIngestionSourceInput!) {
        createIngestionSource(input: $input)
    }
    """
    recipe = _build_recipe(catalog_name, params)
    proxy_args = _compile_proxy_extra_args()
    extra_args_list = [{"key": JUJU_MANAGED_KEY, "value": "True"}]
    for k, v in proxy_args.items():
        extra_args_list.append({"key": k, "value": v})

    variables = {
        "input": {
            "name": _build_ingestion_name(catalog_name),
            "type": "trino",
            "description": f"Juju managed Trino ingestion for {catalog_name}",
            "schedule": {
                "interval": _random_daily_schedule(),
                "timezone": "UTC",
            },
            "config": {
                "recipe": recipe,
                "executorId": "default",
                "extraArgs": extra_args_list,
            },
        }
    }
    body = _graphql_request(query, variables, bearer_token)
    return body["data"]["createIngestionSource"]


def _update_ingestion_source(
    bearer_token: str,
    urn: str,
    existing_source: Dict[str, Any],
    params: "_IngestionParams",
) -> None:
    """Update credentials and proxy vars of an existing ingestion source.

    Preserves name, type, description, schedule, and other settings.

    Args:
        bearer_token: Bearer token for authentication.
        urn: URN of the existing ingestion source.
        existing_source: Current source dict from listIngestionSources.
        params: Common ingestion parameters.
    """
    query = """
    mutation updateIngestionSource($urn: String!, $input: UpdateIngestionSourceInput!) {
        updateIngestionSource(urn: $urn, input: $input)
    }
    """
    recipe = _build_recipe(_extract_catalog_from_name(existing_source["name"]), params)

    # Preserve existing extraArgs but update proxy and JUJU_MANAGED keys.
    existing_extra = existing_source.get("config", {}).get("extraArgs") or []
    proxy_args = _compile_proxy_extra_args()
    managed_keys = {JUJU_MANAGED_KEY, "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}
    preserved = [a for a in existing_extra if a.get("key") not in managed_keys]
    preserved.append({"key": JUJU_MANAGED_KEY, "value": "True"})
    for k, v in proxy_args.items():
        preserved.append({"key": k, "value": v})

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
            "executorId": existing_source.get("config", {}).get("executorId", "default"),
            "extraArgs": preserved,
        },
    }
    if schedule_input:
        input_data["schedule"] = schedule_input

    variables: Dict[str, Any] = {
        "urn": urn,
        "input": input_data,
    }

    _graphql_request(query, variables, bearer_token)


def _delete_ingestion_source(bearer_token: str, urn: str) -> None:
    """Delete an ingestion source from DataHub.

    Args:
        bearer_token: Bearer token for authentication.
        urn: URN of the ingestion source to delete.
    """
    query = """
    mutation deleteIngestionSource($urn: String!) {
        deleteIngestionSource(urn: $urn)
    }
    """
    _graphql_request(query, {"urn": urn}, bearer_token)


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

        self._access_token: Optional[str] = None

    @property
    def access_token(self) -> str:
        """Return (and lazily create) a DataHub access token for ingestion.

        Raises:
            RuntimeError: If token creation fails.
        """
        if self._access_token is None:
            self._access_token = _create_access_token(self.charm.system_client_id, self.charm.system_client_secret)
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
            event.defer()
            return

        self.charm.unit.status = WaitingStatus("handling trino-catalog change")
        self._reconcile_ingestions()
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
            event.defer()
            return

        self._cleanup_managed_ingestions()
        self.charm._update(event)

    def _reconcile_ingestions(self) -> None:
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

        try:
            access_token = self.access_token

            all_sources = _list_ingestion_sources(access_token)
            managed = _filter_juju_managed(all_sources)
        except Exception as e:
            logger.error("Failed to fetch ingestion sources from DataHub: %s", str(e))
            return

        existing_by_catalog: Dict[str, Dict[str, Any]] = {}
        for source in managed:
            catalog_name = _extract_catalog_from_name(source["name"])
            existing_by_catalog[catalog_name] = source

        config = self.charm.config
        params = _IngestionParams(
            trino_url=trino_url,
            username=username,
            password=password,
            access_token=access_token,
            schema_pattern=config.schema_pattern,
            table_pattern=config.table_pattern,
            column_pattern=config.column_pattern,
        )

        self._create_missing_ingestions(desired_catalogs, existing_by_catalog, access_token, params)
        self._update_existing_ingestions(desired_catalogs, existing_by_catalog, access_token, params)
        self._delete_obsolete_ingestions(desired_catalogs, existing_by_catalog, access_token)

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
    ) -> None:
        """Delete ingestion sources for catalogs no longer in the relation."""
        for catalog_name in set(existing.keys()) - desired:
            source = existing[catalog_name]
            try:
                _delete_ingestion_source(bearer, source["urn"])
                logger.info("Deleted obsolete ingestion source for catalog '%s'", catalog_name)
            except Exception as e:
                logger.error("Failed to delete ingestion for catalog '%s': %s", catalog_name, str(e))

    def _cleanup_managed_ingestions(self) -> None:
        """Delete all Juju-managed Trino ingestion sources (relation-broken cleanup)."""
        try:
            access_token = self.access_token
            all_sources = _list_ingestion_sources(access_token)
            managed = _filter_juju_managed(all_sources)
        except Exception as e:
            logger.error("Failed to fetch ingestion sources during cleanup: %s", str(e))
            return

        for source in managed:
            try:
                _delete_ingestion_source(access_token, source["urn"])
                logger.info("Cleaned up ingestion source '%s'", source["name"])
            except Exception as e:
                logger.error("Failed to clean up ingestion '%s': %s", source["name"], str(e))
