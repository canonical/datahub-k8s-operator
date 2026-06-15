# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "oauth_external_idp_integrator" {
  name        = var.app_name
  model_uuid  = var.model_uuid
  units       = var.units
  constraints = var.constraints
  config      = var.config

  charm {
    name     = "oauth-external-idp-integrator"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }
}
