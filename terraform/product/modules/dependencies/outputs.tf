# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "components" {
  description = "Objects representing the deployed data-platform applications."
  value = {
    postgresql               = module.postgresql.application
    kafka                    = module.kafka.application
    zookeeper                = module.zookeeper.application
    opensearch               = module.opensearch.application
    self-signed-certificates = module.self_signed_certificates.application
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

output "provides" {
  description = "Provided endpoints across the data-platform charms (<charm>_<endpoint>)."
  value = {
    postgresql_database                   = module.postgresql.provides.database
    kafka_kafka_client                    = module.kafka.provides.kafka_client
    zookeeper_zookeeper                   = module.zookeeper.provides.zookeeper
    opensearch_opensearch_client          = module.opensearch.provides.opensearch_client
    self_signed_certificates_certificates = module.self_signed_certificates.provides.certificates
  }
}

output "requires" {
  description = "Required endpoints across the data-platform charms (<charm>_<endpoint>)."
  value = {
    kafka_zookeeper         = module.kafka.requires.zookeeper
    opensearch_certificates = module.opensearch.requires.certificates
  }
}
