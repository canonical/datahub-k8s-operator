# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Data-platform component module: composes the per-charm modules and wires their in-model
# integrations and the cross-model offers consumed by DataHub.

### CHARM MODULES

module "postgresql" {
  source = "git::https://github.com/canonical/postgresql-k8s-operator//terraform?ref=v16/1.194.0"

  juju_model         = var.model_uuid
  app_name           = var.postgresql.app_name
  channel            = var.postgresql.channel
  revision           = var.postgresql.revision
  base               = var.postgresql.base
  constraints        = var.postgresql.constraints
  config             = var.postgresql.config
  storage_directives = var.postgresql.storage_directives
  units              = var.postgresql.units
  resources          = var.postgresql.resources
}

# module "kafka" {
#   source = "git::https://github.com/canonical/kafka-k8s-bundle//terraform?ref=0456d03"

#   model_uuid = var.model_uuid
#   broker     = var.kafka_broker
#   controller = var.kafka_controller
#   connect    = var.kafka_connect
#   karapace   = var.kafka_karapace
#   ui         = var.kafka_ui
#   profile    = var.kafka_profile
# }

module "opensearch" {
  source = "../opensearch"

  model_uuid         = var.model_uuid
  app_name           = var.opensearch.app_name
  channel            = var.opensearch.channel
  revision           = var.opensearch.revision
  base               = var.opensearch.base
  constraints        = var.opensearch.constraints
  config             = var.opensearch.config
  storage_directives = var.opensearch.storage_directives
  units              = var.opensearch.units
}

module "self_signed_certificates" {
  source = "git::https://github.com/canonical/self-signed-certificates-operator//terraform?ref=e7527a7"

  model_uuid  = var.model_uuid
  app_name    = var.self_signed_certificates.app_name
  channel     = var.self_signed_certificates.channel
  revision    = var.self_signed_certificates.revision
  base        = var.self_signed_certificates.base
  constraints = var.self_signed_certificates.constraints
  config      = var.self_signed_certificates.config
  units       = var.self_signed_certificates.units
}

### INTEGRATIONS

resource "juju_integration" "opensearch_certificates" {
  model_uuid = var.model_uuid

  application {
    name     = module.opensearch.app_name
    endpoint = module.opensearch.requires.certificates
  }

  application {
    name     = module.self_signed_certificates.app_name
    endpoint = module.self_signed_certificates.provides.certificates
  }
}

### OFFERS

resource "juju_offer" "database" {
  model_uuid       = var.model_uuid
  name             = "database"
  application_name = module.postgresql.application_name
  endpoints        = ["database"]
}

resource "juju_offer" "opensearch_client" {
  model_uuid       = var.model_uuid
  name             = "opensearch-client"
  application_name = module.opensearch.app_name
  endpoints        = ["opensearch-client"]

  depends_on = [juju_integration.opensearch_certificates]
}
