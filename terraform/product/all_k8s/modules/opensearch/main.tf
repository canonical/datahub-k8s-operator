# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "opensearch_k8s" {
  name               = var.app_name
  model_uuid         = var.model_uuid
  units              = var.units
  config             = var.config
  resources          = var.resources
  constraints        = var.constraints
  storage_directives = var.storage_directives

  charm {
    name     = "opensearch-k8s"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  trust = true
}