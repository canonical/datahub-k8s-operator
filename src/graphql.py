# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""DataHub GraphQL API client functions."""

import textwrap
from typing import Any, Dict, List, Optional

import requests

import literals

GRAPHQL_ENDPOINT = f"http://localhost:{literals.GMS_PORT}/api/graphql"


class AuthenticationError(RuntimeError):
    """Raised when a GraphQL request fails due to authentication issues."""


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
        AuthenticationError: If the request fails with a 401/403 or unauthorized GraphQL error.
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
    if response.status_code in (401, 403):
        raise AuthenticationError(f"Authentication failed: HTTP {response.status_code}")
    response.raise_for_status()
    body = response.json()

    if "errors" in body and body["errors"]:
        error_str = str(body["errors"])
        if "unauthorized" in error_str.lower():
            raise AuthenticationError(f"GraphQL authentication error: {body['errors']}")
        raise RuntimeError(f"GraphQL errors: {body['errors']}")

    return body


def create_access_token(system_client_id: str, system_client_secret: str) -> str:
    """Create a DataHub access token for the system user.

    Uses Basic auth with system client credentials to bootstrap a token.

    Args:
        system_client_id: DataHub system client identifier.
        system_client_secret: DataHub system client secret.

    Returns:
        The generated access token string.
    """
    query = textwrap.dedent("""\
        mutation createAccessToken($input: CreateAccessTokenInput!) {
            createAccessToken(input: $input) {
                accessToken
            }
        }""")
    variables = {
        "input": {
            "type": "PERSONAL",
            "actorUrn": f"urn:li:corpuser:{literals.SYSTEM_CLIENT_ID}",
            "duration": "NO_EXPIRY",
            "name": "juju-managed-ingestion-token",
        }
    }
    basic_credentials = f"{system_client_id}:{system_client_secret}"
    body = _graphql_request(query, variables, basic_credentials, auth_scheme="Basic")
    return body["data"]["createAccessToken"]["accessToken"]


def list_secrets(bearer_token: str) -> Dict[str, str]:
    """List all DataHub secrets, returning a name-to-urn mapping.

    Args:
        bearer_token: Bearer token for authentication.

    Returns:
        Dictionary mapping secret names to their URNs.
    """
    query = textwrap.dedent("""\
        query listSecrets($input: ListSecretsInput!) {
            listSecrets(input: $input) {
                total
                secrets {
                    urn
                    name
                }
            }
        }""")
    start = 0
    count = 100
    secrets: Dict[str, str] = {}

    while True:
        variables = {"input": {"start": start, "count": count}}
        body = _graphql_request(query, variables, bearer_token)
        data = body["data"]["listSecrets"]
        for secret in data.get("secrets", []):
            secrets[secret["name"]] = secret["urn"]
        if len(secrets) >= data["total"]:
            break
        start += count

    return secrets


def ensure_secret(
    bearer_token: str,
    name: str,
    value: str,
    existing_secrets: Dict[str, str],
) -> None:
    """Create or update a DataHub-managed secret.

    Args:
        bearer_token: Bearer token for authentication.
        name: Secret name (used as reference in recipes).
        value: Secret plaintext value.
        existing_secrets: Current name-to-urn mapping from list_secrets.
    """
    urn = existing_secrets.get(name)
    if urn:
        query = textwrap.dedent("""\
            mutation updateSecret($input: UpdateSecretInput!) {
                updateSecret(input: $input)
            }""")
        variables = {
            "input": {
                "urn": urn,
                "name": name,
                "value": value,
                "description": "Managed by Juju",
            }
        }
    else:
        query = textwrap.dedent("""\
            mutation createSecret($input: CreateSecretInput!) {
                createSecret(input: $input)
            }""")
        variables = {
            "input": {
                "name": name,
                "value": value,
                "description": "Managed by Juju",
            }
        }
    _graphql_request(query, variables, bearer_token)


def list_ingestion_sources(bearer_token: str) -> List[Dict[str, Any]]:
    """Fetch all ingestion sources from DataHub.

    Args:
        bearer_token: Bearer token for authentication.

    Returns:
        List of ingestion source dictionaries.
    """
    query = textwrap.dedent("""\
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
        }""")
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


def create_ingestion_source(
    bearer_token: str,
    input_data: Dict[str, Any],
) -> str:
    """Create a new ingestion source in DataHub.

    Args:
        bearer_token: Bearer token for authentication.
        input_data: Pre-built UpdateIngestionSourceInput dict.

    Returns:
        URN of the newly created ingestion source.
    """
    query = textwrap.dedent("""\
        mutation createIngestionSource($input: UpdateIngestionSourceInput!) {
            createIngestionSource(input: $input)
        }""")
    variables = {"input": input_data}
    body = _graphql_request(query, variables, bearer_token)
    return body["data"]["createIngestionSource"]


def update_ingestion_source(
    bearer_token: str,
    urn: str,
    input_data: Dict[str, Any],
) -> None:
    """Update an existing ingestion source in DataHub.

    Args:
        bearer_token: Bearer token for authentication.
        urn: URN of the existing ingestion source.
        input_data: Pre-built UpdateIngestionSourceInput dict.
    """
    query = textwrap.dedent("""\
        mutation updateIngestionSource($urn: String!, $input: UpdateIngestionSourceInput!) {
            updateIngestionSource(urn: $urn, input: $input)
        }""")
    variables: Dict[str, Any] = {"urn": urn, "input": input_data}
    _graphql_request(query, variables, bearer_token)


def delete_ingestion_source(bearer_token: str, urn: str) -> None:
    """Delete an ingestion source from DataHub.

    Args:
        bearer_token: Bearer token for authentication.
        urn: URN of the ingestion source to delete.
    """
    query = textwrap.dedent("""\
        mutation deleteIngestionSource($urn: String!) {
            deleteIngestionSource(urn: $urn)
        }""")
    _graphql_request(query, {"urn": urn}, bearer_token)
