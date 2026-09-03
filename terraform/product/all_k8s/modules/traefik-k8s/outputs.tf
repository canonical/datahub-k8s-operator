# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.traefik_k8s
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    ingress = {
      name     = juju_application.traefik_k8s.name
      endpoint = "ingress"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value = {
    certificates = {
      name     = juju_application.traefik_k8s.name
      endpoint = "certificates"
    }
  }
}
