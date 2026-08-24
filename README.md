# DataHub K8s Operator

This is the Kubernetes operator for [DataHub](https://datahubproject.io/), available on [Charmhub](https://charmhub.io/datahub-k8s).

Full documentation for this charm (tutorial, how-to guides, reference, and explanation) lives in the [Canonical Data Mesh documentation](https://github.com/canonical/canonical-data-mesh-docs). Configurations, integrations, and actions are listed on the [Charmhub page](https://charmhub.io/datahub-k8s).

## Description

DataHub is an extensible data catalog that enables data discovery, data observability and federated data governance. It is a component of the Canonical Data Mesh solution.

### Architecture

The charm manages three containers:
- **datahub-gms**: The Generalized Metadata Service. Serves as the backend API, handling metadata storage, retrieval, and search.
- **datahub-frontend**: The web UI. Provides the browser-based interface for data discovery and governance.
- **datahub-actions**: The event processing framework. Handles asynchronous tasks such as notifications, metadata propagation, and ingestion.

## Usage

Note: This operator requires the use of `juju>=3.4`.

The DataHub charm relies on a number of other charms for core functionality:
- [PostgreSQL](https://charmhub.io/postgresql), for storing metadata.
- [Kafka](https://charmhub.io/kafka); for ingestion, message passing, and audit logging.
- [Opensearch](https://charmhub.io/opensearch), for search and graph indexing.

The dependencies in-turn have their own dependencies:
- [Zookeeper](https://charmhub.io/zookeeper), required by Kafka.
- [Self Signed Certificates](https://charmhub.io/self-signed-certificates), required by Opensearch.

### Environment Requirements

The DataHub charm requires a multi-cloud setup:
- DataHub requires a Kubernetes cloud.
- Kafka and PostgreSQL can be deployed on either a Kubernetes or a machine cloud.
- Opensearch requires a machine cloud.

A multi-cloud setup can be achieved with a single controller with multiple clouds registered or two controllers with one cloud registered each. Former will work with cross-model relations which are expected to be more stable than the latter's cross-controller relations.

In this guide, we assume that you have a `datahub-vm` machine model on a `lxd` controller and a `datahub-k8s` Kubernetes model on a `k8s` controller. We also assume that you have sufficient expertise on how to create models on different clouds and how to work with `offer`s to adapt the instructions to your situation. For detailed instructions please refer to the [Contributor's Guide](CONTRIBUTING.md).

### Setting Up the Dependencies

```sh
# Switch to the machine model
juju switch lxd:datahub-vm

# Deploy applications
juju deploy postgresql
juju deploy kafka
juju deploy zookeeper
juju deploy opensearch --channel 2/edge -n 2
juju deploy self-signed-certificates

# Wait for the units to settle
juju status --watch 3s --color

# Relate
juju relate kafka zookeeper
juju relate opensearch self-signed-certificates

# Create named offers
juju offer postgresql:database pg-client
juju offer kafka:kafka-client kafka-client
juju offer opensearch:opensearch-client os-client

# Consume offers from the Kubernetes model
# These will create named saas
juju switch k8s:datahub-k8s
juju consume lxd:admin/datahub-vm.pg-client pg-client
juju consume lxd:admin/datahub-vm.kafka-client kafka-client
juju consume lxd:admin/datahub-vm.os-client os-client
```

### Deploying DataHub

```sh
# Switch to the Kubernetes model
juju switch k8s:datahub-k8s

# Create a secret for encryption keys
juju add-secret <secret-name> gms-key=$GMS_SECRET frontend-key=$FE_SECRET  # Copy the ID from the output

# Deploy
juju deploy datahub-k8s --channel latest/edge --config encryption-keys-secret-id=<secret-id>
juju grant-secret <secret-name> datahub-k8s

# Wait for the unit to settle
juju status --watch 3s --color

# Relate
# Wait between commands for the status to settle into 'active-idle'
juju relate datahub-k8s pg-client
juju relate datahub-k8s kafka-client
juju relate datahub-k8s os-client

# Get the address of DataHub
juju status | grep "^datahub-k8s\s" | grep -P "10\.\d+\.\d+\.\d+" | awk '{print $6}'

# Get the initial admin credentials
# Username is 'datahub' by default
juju run datahub-k8s/0 get-password
```

After relations are set and settled, you can access DataHub at its address with port `9002` from your browser.

#### Deploying with SSO

DataHub supports authentication via SSO. OIDC credentials are received over the `oauth` relation,
either from the [Canonical Identity Platform](https://charmhub.io/identity-platform) (hydra) or
from an [oauth-external-idp-integrator](https://charmhub.io/oauth-external-idp-integrator) bridging
an external IdP. To use an external IdP (Google as the example):

1. Issue credentials from Google Cloud via its [dashboard](https://console.cloud.google.com/apis/credentials).
2. On the linked page, click `Create Credentials` and choose `OAuth client ID`.
3. In the next page, choose `Web application` as the type.
4. Give it a name.
5. Under `Authorized redirect URIs`, add a URI that ends with `/callback/oidc` and begins with the domain used to access the DataHub frontend, e.g. `https://datahub.example.com/callback/oidc`.
6. Get the `Client ID` and the `Client secret`.
7. Deploy the integrator and configure it with the credentials and the IdP endpoints.
   Write the config to a file:
```yaml
# idp-config.yaml
oauth-external-idp-integrator:
  issuer_url: https://accounts.google.com
  authorization_endpoint: https://accounts.google.com/o/oauth2/auth
  token_endpoint: https://oauth2.googleapis.com/token
  introspection_endpoint: https://oauth2.googleapis.com/tokeninfo
  userinfo_endpoint: https://www.googleapis.com/oauth2/v1/userinfo
  jwks_endpoint: https://www.googleapis.com/oauth2/v3/certs
  scope: "openid email profile"
  client_id: <client-id-value>
  client_secret: <client-secret-value>
```
```sh
juju deploy oauth-external-idp-integrator --config idp-config.yaml
```
8. Relate DataHub to the integrator (or directly to hydra when using the Identity Platform):
```sh
juju integrate datahub-k8s:oauth oauth-external-idp-integrator:oauth
```
9. Proceed with the relations. SSO requires the `frontend-ingress` to publish an **HTTPS** URL,
   so integrate the frontend Traefik with a certificates provider; the charm stays blocked on
   `OIDC requires an HTTPS ingress URL` until then.
10. If your deployment is behind an HTTP proxy, configure model proxies as described in [Configuring Model Proxies](#configuring-model-proxies).

Users are matched by the `email` claim, so DataHub user profiles (`urn:li:corpuser:<email>`)
are preserved as long as the same IdP (or one issuing the same emails) backs the relation.

### Deploying with Terraform

The repository ships two Terraform modules:

- [`terraform/charm`](terraform/charm) — a **charm module** for `datahub-k8s` alone, to consume
  from your own Terraform solutions.
- [`terraform/product`](terraform/product) — a **product module** that deploys the full modernized
  stack (PostgreSQL, Kafka + ZooKeeper, OpenSearch, two Traefik ingresses with TLS), creates and
  grants the encryption-keys secret, optionally deploys the OAuth external-IdP integrator for SSO,
  and wires everything together.

### Configuring Model Proxies

If your model runs in a restricted network, configure Juju model proxies so DataHub
containers can reach external services (for example Python package indexes).

```sh
juju model-config juju-http-proxy=http://proxy.example:8080
juju model-config juju-https-proxy=http://proxy.example:8080
juju model-config juju-no-proxy=127.0.0.1,localhost,.svc,.cluster.local
```

Proxy propagation behavior in this charm:
- `datahub-actions` receives standard proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY` and lowercase variants), which are used by Python tooling.
- `datahub-frontend` receives its existing JVM proxy settings.

To verify proxy variables on a container:

```sh
kubectl -n <namespace> exec -c <datahub-actions|datahub-frontend> datahub-k8s-0 -- pebble plan | grep -i '_proxy\|proxy'
```

After changing model proxy settings, allow one `update-status` cycle or trigger a new hook event (for example a config change) so the charm can refresh Pebble plans.

### Migrating Data

Migrating Datahub data entails the migration of metadata stored in PostgreSQL as well as the migration of indices stored in OpenSearch.

#### Migrating Persistent Storage

Migrating persistent storage can be done via juju actions on the PostgreSQL charm as described [here](https://canonical-charmed-postgresql.readthedocs-hosted.com/14/how-to/back-up-and-restore/).

#### Migrating Indices

Migrating indices can be done via juju actions on the Opensearch charm as described [here](https://charmhub.io/opensearch/docs/h-create-backup).

However, it is often easier and sometimes needed to rebuild search indices as outlined in [Datahub documentation](https://docs.datahub.com/docs/how/restore-indices/). This can be done by running the following command:

```bash
juju run datahub-k8s/leader reindex
```

To delete existing indices before rebuilding, use the `clean` parameter:

```bash
juju run datahub-k8s/leader reindex clean=true
```

### Configuring Ingress

The DataHub charm exposes the frontend and GMS services through the standard `ingress` interface, backed by [Traefik](https://charmhub.io/traefik-k8s). Deploy one Traefik instance per ingress endpoint:

```sh
juju deploy traefik-k8s --channel latest/stable --trust traefik-frontend
juju deploy traefik-k8s --channel latest/stable --trust traefik-gms

juju integrate datahub-k8s:frontend-ingress traefik-frontend
juju integrate datahub-k8s:gms-ingress traefik-gms
```

Each Traefik assigns its external URL and publishes it back to the charm via the relation. The frontend OIDC `AUTH_OIDC_BASE_URL` is taken from the `frontend-ingress` URL automatically, so no manual hostname configuration is needed.

To inspect the URLs Traefik has handed out:

```sh
juju run traefik-frontend/0 show-proxied-endpoints
juju run traefik-gms/0      show-proxied-endpoints
```

TLS termination is configured on the Traefik side, see the [Traefik charm docs](https://charmhub.io/traefik-k8s) for how to wire it to a certificates provider (for example [self-signed-certificates](https://charmhub.io/self-signed-certificates) for local environments or [Lego](https://charmhub.io/lego) for ACME providers such as Let's Encrypt).

### Integrating to Trino

The DataHub charm supports a relation to the [Trino charm](https://charmhub.io/trino-k8s). The relation allows Datahub to fetch Trino catalogs and set up scheduled metadata ingestions for each catalog.

Set up as follows:
```sh
juju deploy trino-k8s --channel latest/edge
juju relate datahub-k8s trino-k8s
```

There is a configuration option to set up default patterns for metadata ingestions:
```sh
juju config datahub-k8s trino-patterns='{"schema-pattern":{"allow":[".*"],"deny":[]},"table-pattern":{"allow":[".*"],"deny":[]},"view-pattern":{"allow":[".*"],"deny":[]}}'
```

The option is a string for a JSON object that allows setting up allow and deny patterns for each of schema, table, and views.

The relation only bootstraps ingestions; it never takes them away. On every reconciliation (triggered by relation changes), the charm:
- Creates one ingestion source per Trino catalog that does not have one yet, with names prefixed by `[juju]`.
- Refreshes the Trino host, username, and password of the ingestion sources it already created, along with the HTTP/S proxy variables derived from the model config.
- Deletes nothing, ever.

The full recipe of a new ingestion source is built from the `trino-patterns` config option and a random daily schedule (between 22:00 and 06:00 UTC). On subsequent reconciliations, only the three connection fields are rewritten; the rest of the recipe (filter patterns, environment, sink, profiling, anything else the operator added) and the rest of the source definition (schedule, executor, CLI version, debug mode, other extra arguments) are sent back to DataHub unchanged. Credentials themselves live in DataHub secrets that the recipe references, so rotating them does not touch the recipe.

Recipes edited through the DataHub UI are stored as YAML (the UI submits its editor contents unconverted), while the charm writes JSON when it first creates a source. The charm reads both and writes each one back in the language it arrived in. Comments and indentation do not survive a round trip, so an edited recipe is reformatted the next time the Trino host or credentials actually change.

The proxy variables are the one exception outside the recipe: they live in the ingestion source's `extra_env_vars`, which is the environment the executor runs in rather than ingestion configuration, so the charm keeps them in step with the model config. Changing `juju-http-proxy` and friends propagates to existing ingestion sources, and unsetting them removes the variables. Any other variable in that blob is left alone.

This means an ingestion source can be freely customized via the DataHub UI without the charm overriding those changes. To change the defaults used for *new* ingestion sources, update the `trino-patterns` charm config.

Removing a catalog from the relation, or removing the relation altogether, leaves the ingestion sources and their DataHub secrets in place. That way a redeployment that drops the relation does not destroy the operator's work: when the relation comes back, the existing ingestion sources are reused as-is rather than recreated with default recipes. The trade-off is that ingestion sources for catalogs that are gone for good go stale and have to be deleted by hand from the DataHub UI.

#### Managed Resource Naming Conventions

The charm creates the following resources in DataHub, identifiable by their naming patterns:

- **Ingestion sources**: Named `[juju] <catalog-name>-ingestion` (e.g. `[juju] sales-ingestion`).
- **Per-catalog password secrets**: Named `JUJU_MANAGED_TRINO_PASSWORD_<NORMALIZED_CATALOG>`, where the catalog name is uppercased and non-alphanumeric characters are replaced with `_` (e.g. catalog `my-catalog.test` becomes `JUJU_MANAGED_TRINO_PASSWORD_MY_CATALOG_TEST`).
- **GMS access token secret**: Named `JUJU_MANAGED_GMS_TOKEN`.

The charm never deletes these resources; stale ones are removed by hand. User-created secrets and ingestion sources are not affected if they do not match the charm conventions.

### Serving the API to other charms

The `datahub-client` endpoint (interface `datahub_client`) lets another charm consume the GMS API without anyone handling a token by hand. On integration, DataHub creates a **service account** dedicated to that relation, mints a Personal Access Token for it, stores the token in a Juju secret granted to the relation, and publishes the GMS URL and the secret ID:

```sh
juju integrate datahub-k8s datahub-mcp-k8s
```

The service account gets no privileges of its own, so it inherits DataHub's default all-users policies of metadata read, no writes. Grant it a policy in DataHub if a consumer needs more. Removing the relation deletes the service account, which invalidates every token issued for it.

### Troubleshooting

- **Opensearch offer blocked**: If the Opensearch offer is blocked from the provider end, DataHub will load but some functionalities such as `Ingestion` will not work. This is best identified by requests to `/graphql` returning a `500` error. Ensure the offer is accepted on the provider side.
- **Indices out of sync**: If search results are missing or stale after a migration or upgrade, rebuild the indices with `juju run datahub-k8s/leader reindex`.
- **GMS initialization failure**: The GMS container runs several initialization scripts at startup (PostgreSQL setup, OpenSearch index creation, schema migration). Each script's output is logged to `/tmp` inside the `datahub-gms` container. To list available logs:
```sh
kubectl -n <namespace> exec -c datahub-gms datahub-k8s-0 -- ls /tmp
```
To inspect a specific log:
```sh
kubectl -n <namespace> exec -c datahub-gms datahub-k8s-0 -- cat /tmp/<log-file>
```

## Contributing

This charm is still in active development. Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](CONTRIBUTING.md) for developer guidance.

## License

The Charmed DataHub K8s Operator is free software, distributed under the Apache Software License, version 2.0. See [License](LICENSE) for more details.
