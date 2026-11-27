# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Keep DataHub's garbage collection ingestion source runnable."""

import json
import logging
from typing import Any, Dict, List

import graphql
import literals
import services
from relations.trino import PROXY_ENV_KEYS

logger = logging.getLogger(__name__)


def _apply_proxy_env_vars(extra_args: List[Dict[str, str]]) -> bool:
    """Set the actions service's proxy variables in the source's extra_env_vars.

    The executor installs the source's venv with only these variables in its
    environment, so the proxy the charm sets on the actions service does not
    reach the install otherwise. Other variables are left as they are.

    Args:
        extra_args: The source's extraArgs entries, modified in place.

    Returns:
        True if any variable was changed, False if they were already current.
    """
    desired = services._compile_standard_proxy_environment(extra_no_proxy_hosts=["localhost"])
    for entry in extra_args:
        if entry.get("key") != "extra_env_vars":
            continue
        try:
            env_vars = json.loads(entry.get("value") or "{}")
        except (json.JSONDecodeError, TypeError):
            env_vars = {}
        updated = {**env_vars, **desired}
        for key in PROXY_ENV_KEYS:
            if key not in desired:
                updated.pop(key, None)
        if updated == env_vars:
            return False
        entry["value"] = json.dumps(updated)
        return True
    if not desired:
        return False
    extra_args.append({"key": "extra_env_vars", "value": json.dumps(desired)})
    return True


def reconcile_gc_source(system_client_id: str, system_client_secret: str) -> bool:
    """Fix the proxy variables and CLI version of the `datahub-gc` ingestion source.

    The bootstrap template writes the source with no extra_env_vars and an
    empty `version`, which the executor turns into the unsatisfiable
    requirement `acryl-datahub[datahub-gc]==`. The empty version is removed so
    GMS sends its default CLI version. Everything else, including
    `source.type`, is sent back unchanged because the update replaces the
    whole source definition.

    Args:
        system_client_id: DataHub system client identifier.
        system_client_secret: DataHub system client secret.

    Returns:
        True if an update was sent, False if the source is absent or already current.
    """
    credentials = f"{system_client_id}:{system_client_secret}"
    urn = literals.GC_INGESTION_SOURCE_URN
    source = graphql.get_ingestion_source(credentials, urn, auth_scheme="Basic")
    if source is None:
        logger.debug("Ingestion source '%s' does not exist, nothing to reconcile", urn)
        return False

    config = source.get("config") or {}
    extra_args = [{"key": arg["key"], "value": arg["value"]} for arg in config.get("extraArgs") or []]
    env_changed = _apply_proxy_env_vars(extra_args)
    version = config.get("version")
    if not env_changed and version != "":
        return False

    config_input: Dict[str, Any] = {
        "recipe": config["recipe"],
        "executorId": config.get("executorId") or literals.DEFAULT_EXECUTOR_ID,
        "extraArgs": extra_args,
    }
    if version:
        config_input["version"] = version
    if config.get("debugMode") is not None:
        config_input["debugMode"] = config["debugMode"]

    input_data: Dict[str, Any] = {
        "name": source["name"],
        "type": source["type"],
        "config": config_input,
    }
    schedule = source.get("schedule")
    if schedule:
        input_data["schedule"] = {
            "interval": schedule["interval"],
            "timezone": schedule.get("timezone") or "UTC",
        }
    if source.get("source"):
        input_data["source"] = {"type": source["source"]["type"]}

    graphql.update_ingestion_source(credentials, urn, input_data, auth_scheme="Basic")
    return True
