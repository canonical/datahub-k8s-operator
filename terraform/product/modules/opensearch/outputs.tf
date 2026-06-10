# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.opensearch
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    opensearch_client = {
      name     = juju_application.opensearch.name
      endpoint = "opensearch-client"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value = {
    certificates = {
      name     = juju_application.opensearch.name
      endpoint = "certificates"
    }
  }
}
