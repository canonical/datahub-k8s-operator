# DataHub K8s Operator

This is the Kubernetes operator for [DataHub](https://datahubproject.io/), available on [Charmhub](https://charmhub.io/datahub-k8s).

## Description

DataHub is an extensible data catalog that enables data discovery, data observability and federated data governance.

### Architecture

The charm manages three containers:
- **datahub-gms**: The Generalized Metadata Service. Serves as the backend API, handling metadata storage, retrieval, and search.
- **datahub-frontend**: The web UI. Provides the browser-based interface for data discovery and governance.
- **datahub-actions**: The event processing framework. Handles asynchronous tasks such as notifications, metadata propagation, and ingestion.

## Usage

Note: This operator requires the use of `juju>=3.3`.

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
juju deploy self-signed-certificates-operator

# Wait for the units to settle
juju status --watch 3s --color

# Relate
juju relate kafka zookeeper
juju relate opensearch self-signed-certificates-operator

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

DataHub supports authentication via SSO. In order to enable it in the charm follow the steps:

1. Issue credentials from Google Cloud via its [dashboard](https://console.cloud.google.com/apis/credentials).
2. On the linked page, click `Create Credentials` and choose `OAuth client ID`.
3. In the next page, choose `Web application` as the type.
4. Give it a name.
5. Under `Authorized redirect URIs`, add a URI that ends with `/callback/oidc` and begins with the domain used to access the DataHub frontend. For local deployment the complete URI would look like `http://localhost:9002/callback/oidc`.
6. Get the `Client ID` and the `Client secret`.
7. Create a YAML file of the following format:
```yaml
client-id: <client-id-value>
client-secret: <client-secret-value>
```
8. Create a Juju secret.
9. Go into the Juju model where DataHub is (to be) deployed.
10. Run `juju add-secret <secret-name> --file=/path/to/yaml` and copy the secret ID.
11. Deploy DataHub with the added config variable `--config oidc-secret-id=<secret-id>`.
12. Run `juju grant-secret <secret-name> datahub-k8s` to set permissions.
13. Proceed with the relations.
14. If your deployment is behind an HTTP proxy, configure model proxies as described in [Configuring Model Proxies](#configuring-model-proxies).

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

The DataHub charm supports exposing the frontend and GMS services via [Nginx Ingress Integrator](https://charmhub.io/nginx-ingress-integrator).

```sh
juju deploy nginx-ingress-integrator --channel latest/edge --trust
juju relate datahub-k8s:nginx-fe-route nginx-ingress-integrator
```

You can configure the external hostnames with:

```sh
juju config datahub-k8s external-fe-hostname=datahub.example.com
juju config datahub-k8s external-gms-hostname=datahub-gms.example.com
```

To enable TLS, you can either create a Kubernetes TLS secret manually and pass its name to the charm:

```sh
juju config datahub-k8s tls-secret-name=<k8s-tls-secret-name>
```

Alternatively, you can use the [Lego](https://charmhub.io/lego) charm to automate certificate management via ACME providers such as Let's Encrypt.

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

The charm creates one ingestion source per Trino catalog with names prefixed by `[juju]`.

On every reconciliation (triggered by relation changes), the charm will overwrite the following fields in each managed ingestion source:
- Access tokens (stored as DataHub secrets)
- Trino credentials (username and password, stored as DataHub secrets)
- Trino host, port, and catalog name
- HTTP/S proxy variables derived from the model config

The following are set only during the initial creation of an ingestion source and preserved on subsequent updates:
- Filter patterns from the `trino-patterns` config option
- A random daily schedule (between 22:00 and 06:00 UTC)

Because patterns are only applied on creation, they can be freely customized via the DataHub UI afterwards. To change the default patterns used for new ingestion sources, update the `trino-patterns` charm config.

The schedule, description, executor, and any non-managed extra arguments can be freely updated via the DataHub UI without interference from the charm.

When a catalog is removed from the Trino relation, its corresponding ingestion source is automatically deleted. When the relation is fully broken, all Juju-managed ingestion sources are cleaned up. Note that cleaning the ingestions does not remove already ingested metadata.

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
