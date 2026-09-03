# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_name" {
  description = "Name of the deployed application"
  value       = juju_application.opensearch_k8s.name
}

output "provides" {
  description = "Map of provided endpoints"
  value = {
    opensearch_client = "opensearch-client"
  }
}

output "requires" {
  description = "Map of required endpoints"
  value = {
    certificates   = "certificates"
    cos            = "cos-agent"
    s3_credentials = "s3-credentials"
  }
}