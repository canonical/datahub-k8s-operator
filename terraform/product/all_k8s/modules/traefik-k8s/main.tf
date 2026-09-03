# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "traefik_k8s" {
  name       = var.app_name
  model_uuid = var.model_uuid
  units      = var.units
  trust      = var.trust
  config     = var.config

  charm {
    name     = "traefik-k8s"
    channel  = var.channel
    revision = var.revision
  }
}
