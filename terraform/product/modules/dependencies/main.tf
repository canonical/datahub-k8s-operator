# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Data-platform component module: composes the per-charm modules and wires their in-model
# integrations and the cross-model offers consumed by DataHub.
#
# The charm modules below are local stopgaps that adhere to the CC008 charm-module interface.
# Once the upstream charms publish official Terraform modules, swap each `source` to the pinned
# upstream reference.

### CHARM MODULES

module "postgresql" {
  source = "../postgresql"

  model_uuid         = var.model_uuid
  app_name           = var.postgresql.app_name
  channel            = var.postgresql.channel
  revision           = var.postgresql.revision
  base               = var.postgresql.base
  constraints        = var.postgresql.constraints
  config             = var.postgresql.config
  storage_directives = var.postgresql.storage_directives
  units              = var.postgresql.units
}

module "kafka" {
  source = "../kafka"

  model_uuid         = var.model_uuid
  app_name           = var.kafka.app_name
  channel            = var.kafka.channel
  revision           = var.kafka.revision
  base               = var.kafka.base
  constraints        = var.kafka.constraints
  config             = var.kafka.config
  storage_directives = var.kafka.storage_directives
  units              = var.kafka.units
}

module "zookeeper" {
  source = "../zookeeper"

  model_uuid         = var.model_uuid
  app_name           = var.zookeeper.app_name
  channel            = var.zookeeper.channel
  revision           = var.zookeeper.revision
  base               = var.zookeeper.base
  constraints        = var.zookeeper.constraints
  config             = var.zookeeper.config
  storage_directives = var.zookeeper.storage_directives
  units              = var.zookeeper.units
}

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
  source = "../self-signed-certificates"

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

resource "juju_integration" "kafka_zookeeper" {
  model_uuid = var.model_uuid

  application {
    name     = module.kafka.requires.zookeeper.name
    endpoint = module.kafka.requires.zookeeper.endpoint
  }

  application {
    name     = module.zookeeper.provides.zookeeper.name
    endpoint = module.zookeeper.provides.zookeeper.endpoint
  }
}

resource "juju_integration" "opensearch_certificates" {
  model_uuid = var.model_uuid

  application {
    name     = module.opensearch.requires.certificates.name
    endpoint = module.opensearch.requires.certificates.endpoint
  }

  application {
    name     = module.self_signed_certificates.provides.certificates.name
    endpoint = module.self_signed_certificates.provides.certificates.endpoint
  }
}

### OFFERS

resource "juju_offer" "database" {
  model_uuid       = var.model_uuid
  name             = "database"
  application_name = module.postgresql.application.name
  endpoints        = ["database"]
}

resource "juju_offer" "kafka_client" {
  model_uuid       = var.model_uuid
  name             = "kafka-client"
  application_name = module.kafka.application.name
  endpoints        = ["kafka-client"]

  depends_on = [juju_integration.kafka_zookeeper]
}

resource "juju_offer" "opensearch_client" {
  model_uuid       = var.model_uuid
  name             = "opensearch-client"
  application_name = module.opensearch.application.name
  endpoints        = ["opensearch-client"]

  depends_on = [juju_integration.opensearch_certificates]
}
