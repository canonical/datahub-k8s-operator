# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "components" {
  description = "Names of the deployed data-platform applications, keyed by role."
  value = {
    postgresql               = module.postgresql.application_name
    opensearch               = module.opensearch.app_name
    self-signed-certificates = module.self_signed_certificates.app_name
  }
}

output "offers" {
  description = "Cross-model offer URLs for the data-platform endpoints consumed by DataHub."
  value = {
    database          = juju_offer.database.url
    opensearch_client = juju_offer.opensearch_client.url
  }
}

output "provides" {
  description = "Provided endpoints across the data-platform charms (<charm>_<endpoint>)."
  value = {
    postgresql_database                   = module.postgresql.provides.database
    opensearch_client                     = module.opensearch.provides.opensearch_client
    self_signed_certificates_certificates = module.self_signed_certificates.provides.certificates
  }
}

output "requires" {
  description = "Required endpoints across the data-platform charms (<charm>_<endpoint>)."
  value = {
    opensearch_certificates = module.opensearch.requires.certificates
  }
}
