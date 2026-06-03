# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_name" {
  description = "Name of the deployed application."
  value       = juju_application.datahub_k8s.name
}

output "requires" {
  description = "Map of the charm's `requires` integration endpoints."
  value = {
    db               = "db"
    kafka            = "kafka"
    opensearch       = "opensearch"
    frontend_ingress = "frontend-ingress"
    gms_ingress      = "gms-ingress"
    trino_catalog    = "trino-catalog"
  }
}
