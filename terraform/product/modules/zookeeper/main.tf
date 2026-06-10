# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "zookeeper" {
  name        = var.app_name
  model_uuid  = var.model_uuid
  units       = var.units
  constraints = var.constraints
  config      = var.config

  storage_directives = var.storage_directives

  charm {
    name     = "zookeeper"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }
}
