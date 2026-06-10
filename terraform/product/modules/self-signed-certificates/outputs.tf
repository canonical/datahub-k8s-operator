# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.self_signed_certificates
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    certificates = {
      name     = juju_application.self_signed_certificates.name
      endpoint = "certificates"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value       = {}
}
