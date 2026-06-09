# DataHub product Terraform module

This folder contains a [Terraform][Terraform] **product module** that deploys a full, modernized
DataHub solution: the `datahub-k8s` charm (via the [charm module](../charm)), its data platform
(PostgreSQL, Kafka + ZooKeeper, OpenSearch + self-signed-certificates), two Traefik ingresses
(frontend + GMS) with TLS, and the Juju secrets DataHub needs.

## Topology

This is a **single-controller** module: one Juju controller with both a machine cloud and a K8s
cloud (e.g. LXD + Canonical K8s). DataHub is a K8s charm; its data platform (PostgreSQL, Kafka,
OpenSearch) are **machine** charms, so they live in a separate machine-cloud model and are consumed
via cross-model offers. Two modes:

- **Deploy the data platform (default):** leave the `*_offer_url` inputs empty. The module deploys
  the data platform in `machine_model_uuid`, creates cross-model offers, and consumes them from
  `k8s_model_uuid`. One `terraform apply` brings up the whole stack.
- **Bring your own data platform:** point `database_offer_url` / `kafka_offer_url` /
  `opensearch_offer_url` at an existing data platform offered from another model **on the same
  controller**. The in-module data-platform deploy is then skipped and DataHub just consumes the
  offers.

> Set the three `*_offer_url` inputs together (all or none, enforced by variable validation).

## Secrets

The module creates and grants the two Juju secrets DataHub reads:

| Secret | Content | Do you supply values? |
|--------|---------|------------------------|
| `datahub-encryption-keys` | `gms-key`, `frontend-key` | **No**, random values are generated (override via `encryption_keys` only to match an existing deployment). |
| `datahub-oidc` (optional) | `client-id`, `client-secret` | **Only for SSO**, pass the `oidc` variable with real OAuth credentials. Omit (`null`) to disable SSO. |

> Generated keys and any OIDC credentials are stored in Terraform state. 
> Use an encrypted / remote backend in real deployments.

## Notes & caveats

- **Admin password:** not a Terraform output. Retrieve it with
  `juju run datahub-k8s/0 get-password`. The proxied URL comes from
  `juju run traefik-frontend/0 show-proxied-endpoints`.
- **Multi-user controllers:** when the data platform and DataHub models are owned by different
  users, grant the offers with `juju grant` (a single-admin controller needs no grant).

## Module structure

This product module is composed entirely of **charm modules** and a **component module**.

- **main.tf** - composes the data-platform component, the DataHub charm module, the ingress
  (traefik) and TLS (self-signed-certificates) charm modules; creates the secrets and all integrations.
- **variables.tf** - model UUIDs, per-charm configuration objects, offer-URL toggles, secret inputs.
- **outputs.tf** - `models`, `metadata`, `offers`.
- **locals.tf** - deploy/offer resolution and DataHub config assembly.
- **terraform.tf** - Terraform and provider version constraints.
- **modules/{postgresql,kafka,zookeeper,opensearch,self-signed-certificates,traefik-k8s}** - local
  **charm modules**: swap each `source` to the official upstream charm module once it is published.
- **modules/dependencies** - the data-platform **component module**: composes the data-platform
  charm modules above, wires their integrations, and exposes the cross-model offers.
