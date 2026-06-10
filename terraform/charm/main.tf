# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "datahub_k8s" {
  name       = var.app_name
  model_uuid = var.model_uuid

  charm {
    name     = "datahub-k8s"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  config      = { for k, v in var.config : k => v if v != null }
  constraints = var.constraints
  resources   = var.resources
  units       = var.units
}
