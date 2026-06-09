# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.postgresql
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    database = {
      name     = juju_application.postgresql.name
      endpoint = "database"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value       = {}
}
