[![Charmhub Badge](https://charmhub.io/datahub-k8s/badge.svg)](https://charmhub.io/datahub-k8s)
[![Release Edge](https://github.com/canonical/datahub-k8s-operator/actions/workflows/publish_charm.yaml/badge.svg)](https://github.com/canonical/datahub-k8s-operator/actions/workflows/publish_charm.yaml)

# DataHub K8s Operator

This is the Kubernetes operator for [DataHub](https://datahubproject.io/), available on [Charmhub](https://charmhub.io/datahub-k8s).

Full documentation for this charm (tutorial, how-to guides, reference, and explanation) lives in the [Canonical Data Mesh documentation](https://canonical-data-mesh-documentation.readthedocs-hosted.com/en/latest/). Configuration options, integrations, and actions are listed on the [Charmhub page](https://charmhub.io/datahub-k8s).

## Description

DataHub is an extensible data catalog that enables data discovery, data observability and federated data governance. It gathers metadata such as tables, topics, schemas, ownership and lineage from your data systems and serves it through a searchable web interface and a rich API. It is a component of the Canonical Data Mesh solution.

It is intended for data platform teams who want a governed, discoverable data landscape without the complexity of manual deployment and ongoing service management.

## Deployment

The charm manages a single pod with three containers:

- `datahub-gms`: the Generalized Metadata Service, the backend API that stores, indexes and serves metadata on port 8080.
- `datahub-frontend`: the web UI on port 9002.
- `datahub-actions`: the event processing framework that runs ingestion recipes and other asynchronous tasks.

## Usage

The charm requires `juju>=3.4` and:

- [PostgreSQL](https://charmhub.io/postgresql), for storing metadata.
- [Kafka](https://charmhub.io/kafka), for ingestion, message passing and audit logging.
- [OpenSearch](https://charmhub.io/opensearch), for search and graph indexing.

It optionally integrates with an ingress provider over the `ingress` interface (once for the frontend, once for the GMS API), an identity provider over `oauth`, [Trino](https://charmhub.io/trino-k8s) over `trino-catalog`, API consumers such as [the DataHub MCP server](https://charmhub.io/datahub-mcp-k8s) over `datahub-client`, and the Canonical Observability Stack.

```bash
juju add-secret datahub-encryption-keys gms-key=<GMS_KEY> frontend-key=<FRONTEND_KEY>
juju deploy datahub-k8s --config encryption-keys-secret-id=<SECRET_ID>
juju grant-secret datahub-encryption-keys datahub-k8s
```

See the [DataHub tutorial](https://canonical-data-mesh-documentation.readthedocs-hosted.com/en/latest/tutorials/datahub/) for a complete walkthrough, and the [how-to guides](https://canonical-data-mesh-documentation.readthedocs-hosted.com/en/latest/how-to/datahub/) for exposing it with ingress, enabling single sign-on, connecting it to Trino, backing it up, observing it, and troubleshooting.

## Terraform modules

The repository ships two Terraform modules:

- [`terraform/charm`](terraform/charm): a charm module for `datahub-k8s` alone, to consume from your own Terraform solutions.
- [`terraform/product`](terraform/product): a product module that deploys the full stack (PostgreSQL, Kafka and ZooKeeper, OpenSearch, two Traefik ingresses with TLS), creates and grants the encryption-keys secret, optionally deploys the OAuth external-IdP integrator for SSO, and wires everything together.

See [Deploy with Terraform](https://canonical-data-mesh-documentation.readthedocs-hosted.com/en/latest/how-to/datahub/deploy-with-terraform/) for how to use them.

## Contributing

Please see [CONTRIBUTING.md](CONTRIBUTING.md) for developer setup, build instructions, and how to deploy the charm locally from source. Security issues are handled as described in [SECURITY.md](SECURITY.md).

## Project and Community

Charmed DataHub is a member of the Ubuntu family. It is an open source project that warmly welcomes community projects, contributions, suggestions, fixes and constructive feedback.

- [Read our Code of Conduct](https://ubuntu.com/community/code-of-conduct).
- [Join the Discourse forum](https://discourse.charmhub.io/).
- [Contribute and report bugs](https://github.com/canonical/datahub-k8s-operator).

## License

The Charmed DataHub K8s Operator is free software, distributed under the Apache Software License, version 2.0. See [LICENSE](LICENSE) for more details.
