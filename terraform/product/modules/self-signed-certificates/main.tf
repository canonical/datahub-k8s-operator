# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "self_signed_certificates" {
  name        = var.app_name
  model_uuid  = var.model_uuid
  units       = var.units
  constraints = var.constraints
  config      = var.config

  charm {
    name     = "self-signed-certificates"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }
}
