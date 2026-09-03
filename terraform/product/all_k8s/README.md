# DataHub all-Kubernetes product Terraform module

This folder contains a Terraform **product module** that deploys a full, modernized
DataHub solution: the `datahub-k8s` charm (via the [charm module](../../charm)), its data platform
(PostgreSQL, Kafka, OpenSearch + self-signed-certificates), two Traefik ingresses
(frontend + GMS) with TLS, the Juju secrets DataHub needs, and optionally an IdP integrator for SSO.

It differs from the [sibling product module](../README.md) in one respect: every charm it deploys
is a Kubernetes charm, so the data platform needs no machine cloud. Kafka runs in KRaft mode, so
there is no ZooKeeper application.

## Topology

This is a **single-controller** module: one Juju controller with two Kubernetes models (e.g.
`data-platform` and `datahub`), both on the same K8s cloud. DataHub consumes PostgreSQL and
OpenSearch from the data-platform model via cross-model offers.

Kafka is the exception: it is deployed into the DataHub model and related in-model, because
`kafka-k8s` cannot currently serve its `kafka-client` endpoint over a cross-model relation. It is
still part of the data platform as far as this module's inputs go, so it is deployed and skipped
together with the rest.

Two modes:

- **Deploy the data platform (default):** leave the `*_offer_url` inputs empty. The module deploys
  PostgreSQL and OpenSearch in `data_platform_model_uuid` and offers them, Kafka in
  `k8s_model_uuid`, and consumes all three from `k8s_model_uuid`. One `terraform apply` brings up
  the whole stack.
- **Bring your own data platform:** point `database_offer_url` / `kafka_offer_url` /
  `opensearch_offer_url` at an existing data platform offered from another model **on the same
  controller**. Nothing is deployed in-module and DataHub just consumes the offers. Note that
  `kafka_offer_url` runs into the same cross-model limitation; it is wired for when that is fixed.

> Set the three `*_offer_url` inputs together (all or none, enforced by variable validation).

## Secrets

The module creates and grants the Juju secret DataHub reads:

| Secret | Content | Do you supply values? |
|--------|---------|------------------------|
| `datahub-encryption-keys` | `gms-key`, `frontend-key` | **No**, random values are generated (override via `encryption_keys` only to match an existing deployment). |

> Generated keys and the IdP credentials in the integrator's app config are stored in Terraform
> state. Use an encrypted / remote backend in real deployments.

## SSO via the `oauth` relation

To enable SSO, set `oauth_external_idp_integrator_config` (at minimum `client_id` and `client_secret`; the endpoint
options default to Google). The module then deploys [oauth-external-idp-integrator](https://charmhub.io/oauth-external-idp-integrator)
and relates it to DataHub on the `oauth` interface, which delivers the issuer URL and client
credentials over the relation. Leave the variable `null` to disable SSO. Alternatively, relate
DataHub directly to a [Canonical Identity Platform](https://charmhub.io/identity-platform) hydra outside this module.

Notes:

- The frontend ingress must publish an **HTTPS** URL before the charm accepts the oauth relation
  (integrate the Traefiks with a certificates provider, as this module does).
- The integrator's `oauth` endpoint accepts a single requirer: one DataHub per integrator.

## Notes & caveats

- **Admin password:** not a Terraform output. Retrieve it with
  `juju run datahub-k8s/0 get-password`. The proxied URL comes from
  `juju run traefik-frontend/0 show-proxied-endpoints`.
- **Multi-user controllers:** when the data platform and DataHub models are owned by different
  users, grant the offers with `juju grant` (a single-admin controller needs no grant).

## Running the module tests

`terraform test` defaults match CI ([operator-workflows](https://github.com/canonical/operator-workflows) registers the K8s
cloud as `tfk8s` on the LXD controller; storage uses the cluster's default StorageClass). To run
locally against a different setup, pass globals via the environment. For example MicroK8s, needs an explicit StorageClass:

```sh
TF_VAR_k8s_cloud_name=microk8s TF_VAR_k8s_credential_name=microk8s \
TF_VAR_k8s_workload_storage=microk8s-hostpath terraform test
```

## Module structure

This product module is composed entirely of **charm modules** and a **component module**.

- **main.tf** - composes the data-platform component, the DataHub charm module, the ingress
  (traefik), TLS (self-signed-certificates) and OAuth-integrator charm modules; creates the secrets
  and all integrations.
- **variables.tf** - model UUIDs, per-charm configuration objects, offer-URL toggles, secret inputs.
- **outputs.tf** - `models`, `metadata`, `offers`.
- **locals.tf** - deploy/offer resolution and DataHub config assembly.
- **terraform.tf** - Terraform and provider version constraints.
- **modules/{opensearch,traefik-k8s,oauth-external-idp-integrator}** - local **charm modules**:
  swap each `source` to the official upstream charm module once one is published. PostgreSQL, Kafka
  and self-signed-certificates already come from their upstream modules, pinned by tag or commit.
- **modules/dependencies** - the data-platform **component module**: composes the data-platform
  charm modules above, wires their integrations, and exposes the cross-model offers.
