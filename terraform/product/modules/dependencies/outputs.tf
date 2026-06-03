# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "app_names" {
  description = "Names of the deployed data-platform applications."
  value = {
    postgresql               = juju_application.postgresql.name
    kafka                    = juju_application.kafka.name
    zookeeper                = juju_application.zookeeper.name
    opensearch               = juju_application.opensearch.name
    self-signed-certificates = juju_application.self_signed_certificates.name
  }
}

output "offers" {
  description = "Cross-model offer URLs for the data-platform endpoints consumed by DataHub."
  value = {
    database          = juju_offer.database.url
    kafka_client      = juju_offer.kafka_client.url
    opensearch_client = juju_offer.opensearch_client.url
  }
}
