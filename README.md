# DataHub K8s Operator

This is the Kubernetes operator for [DataHub](https://datahubproject.io/).

## Description

DataHub is an extensible data catalog that enables data discovery, data observability and federated data governance.

This operator provides DataHub services on Kubernetes and consists of Python scripts which wrap the [distributions](https://hub.docker.com/u/acryldata) from DataHub.

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
echo "gms-key: $GMS_SECRET\nfrontend-key: $FE_SECRET\n" > /path/to/secret.yaml
juju add-secret <secret-name> --file=/path/to/secret.yaml  # Copy the ID from the output

# Deploy
juju deploy datahub-k8s --config encryption-key-secret-id=<secret-id>

# Wait for the unit to settle
juju status --watch 3s --color

# Relate
# Wait between commands for the status to settle into 'active-idle'
juju relate datahub-k8s pg-client
juju relate datahub-k8s kafka-client
juju relate datahub-k8s os-client

# Get the address of DataHub
juju status | grep "^datahub-k8s\s" | grep -P "10\.\d+\.\d+\.\d+" | awk '{print $6}'
```

After relations are set and settled, you can access DataHub at its address with port `9002` from your browser.

#### Deploying with SSO

DataHub supports authentication via SSO. In order to enable it in the charm follow the steps:

1. Issue credentials from Google Cloud via its [dashboard](https://console.cloud.google.com/apis/credentials).
1.1. On the linked page, click `Create Credentials` and choose `OAuth client ID`.
1.2. In the next page, choose `Web application` as the type.
1.3. Give it a name.
1.4. Under `Authorized redirect URIs`, add a URI that ends with `/callback/oidc` and begins with the domain used to access the DataHub frontend. For local deployment the complete URI would look like `http://localhost:9002/callback/oidc`.
1.5. Get the `Client ID` and the `Client secret`.
1.6. Create a YAML file of the following format:
```yaml
client-id: <client-id-value>
client-secret: <client-secret-value>
```
2. Create a Juju secret.
2.1. Go into the Juju model where DataHub is (to be) deployed.
2.2. Run `juju add-secret <secret-name> --file=/path/to/yaml` and copy the secret ID.
2.3. Deploy DataHub with the added config variable `--config oidc-secret-id=<secret-id>`.
2.4. Run `juju grant-secret <secret-name> datahub-k8s` to set permissions.
2.5. Proceed with the relations.
3. If your deployment is behind a HTTP proxy, set it on your Juju model via
```sh
juju model-config juju-http-proxy=http://squid.internal:3128
juju model-config juju-https-proxy=http://squid.internal:3128
```

## Contributing
This charm is still in active development. Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and [CONTRIBUTING.md](CONTRIBUTING.md) for developer guidance.

## License
The Charmed DataHub K8s Operator is free software, distributed under the Apache Software License, version 2.0. See [License](LICENSE) for more details.
