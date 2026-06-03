# DataHub charm Terraform module

This folder contains a base [Terraform][Terraform] module for the `datahub-k8s` charm.

The module uses the [Terraform Juju provider][Terraform Juju provider] to model the charm
deployment onto any Kubernetes environment managed by Juju. It models the
`datahub-k8s` application only without its dependencies (PostgreSQL, Kafka, OpenSearch, ingress)
are wired up by the higher-level [product module](../product).

## Module structure

- **main.tf** - Defines the `datahub-k8s` Juju application.
- **variables.tf** - Deployment options (model UUID, channel, revision, config, resources, …).
- **outputs.tf** - The application name and the `requires` integration endpoints.
- **terraform.tf** - Terraform and provider version constraints.

## Using `datahub-k8s` base module in higher level modules

```text
module "datahub" {
  source     = "git::https://github.com/canonical/datahub-k8s-operator//terraform/charm"
  model_uuid = var.model_uuid
  # (Customize configuration variables here if needed)
}
```

Create integrations, for instance against Traefik:

```text
resource "juju_integration" "datahub_frontend_ingress" {
  model_uuid = var.model_uuid
  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.frontend_ingress
  }
  application {
    name     = "traefik-frontend"
    endpoint = "ingress"
  }
}
```

The complete list of available integrations can be found [in the Integrations tab][datahub-integrations].

[Terraform]: https://www.terraform.io/
[Terraform Juju provider]: https://registry.terraform.io/providers/juju/juju/latest
[datahub-integrations]: https://charmhub.io/datahub-k8s/integrations
