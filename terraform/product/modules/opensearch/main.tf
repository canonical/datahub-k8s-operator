# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "opensearch" {
  name        = var.app_name
  model_uuid  = var.model_uuid
  units       = var.units
  constraints = var.constraints
  config      = var.config

  storage_directives = var.storage_directives

  charm {
    name     = "opensearch"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  # Expose opensearch-client so the cross-model offer is reachable from the consuming model.
  expose {
    endpoints = "opensearch-client"
  }
}
