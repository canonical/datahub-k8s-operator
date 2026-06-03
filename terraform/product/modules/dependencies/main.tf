# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

### APPLICATIONS

resource "juju_application" "postgresql" {
  name        = var.postgresql.app_name
  model_uuid  = var.model_uuid
  units       = var.postgresql.units
  constraints = var.postgresql.constraints
  config      = var.postgresql.config

  charm {
    name     = "postgresql"
    channel  = var.postgresql.channel
    revision = var.postgresql.revision
    base     = var.postgresql.base
  }
}

resource "juju_application" "kafka" {
  name        = var.kafka.app_name
  model_uuid  = var.model_uuid
  units       = var.kafka.units
  constraints = var.kafka.constraints
  config      = var.kafka.config

  charm {
    name     = "kafka"
    channel  = var.kafka.channel
    revision = var.kafka.revision
    base     = var.kafka.base
  }

  expose {
    endpoints = "kafka-client"
  }
}

resource "juju_application" "zookeeper" {
  name        = var.zookeeper.app_name
  model_uuid  = var.model_uuid
  units       = var.zookeeper.units
  constraints = var.zookeeper.constraints
  config      = var.zookeeper.config

  charm {
    name     = "zookeeper"
    channel  = var.zookeeper.channel
    revision = var.zookeeper.revision
    base     = var.zookeeper.base
  }
}

resource "juju_application" "opensearch" {
  name        = var.opensearch.app_name
  model_uuid  = var.model_uuid
  units       = var.opensearch.units
  constraints = var.opensearch.constraints
  config      = var.opensearch.config

  charm {
    name     = "opensearch"
    channel  = var.opensearch.channel
    revision = var.opensearch.revision
    base     = var.opensearch.base
  }

  expose {
    endpoints = "opensearch-client"
  }
}

resource "juju_application" "self_signed_certificates" {
  name        = var.self_signed_certificates.app_name
  model_uuid  = var.model_uuid
  units       = var.self_signed_certificates.units
  constraints = var.self_signed_certificates.constraints
  config      = var.self_signed_certificates.config

  charm {
    name     = "self-signed-certificates"
    channel  = var.self_signed_certificates.channel
    revision = var.self_signed_certificates.revision
    base     = var.self_signed_certificates.base
  }
}

### INTEGRATIONS

resource "juju_integration" "kafka_zookeeper" {
  model_uuid = var.model_uuid

  application {
    name     = juju_application.kafka.name
    endpoint = "zookeeper"
  }

  application {
    name     = juju_application.zookeeper.name
    endpoint = "zookeeper"
  }
}

resource "juju_integration" "opensearch_certificates" {
  model_uuid = var.model_uuid

  application {
    name     = juju_application.opensearch.name
    endpoint = "certificates"
  }

  application {
    name     = juju_application.self_signed_certificates.name
    endpoint = "certificates"
  }
}

### OFFERS

resource "juju_offer" "database" {
  model_uuid       = var.model_uuid
  name             = "database"
  application_name = juju_application.postgresql.name
  endpoints        = ["database"]
}

resource "juju_offer" "kafka_client" {
  model_uuid       = var.model_uuid
  name             = "kafka-client"
  application_name = juju_application.kafka.name
  endpoints        = ["kafka-client"]

  depends_on = [juju_integration.kafka_zookeeper]
}

resource "juju_offer" "opensearch_client" {
  model_uuid       = var.model_uuid
  name             = "opensearch-client"
  application_name = juju_application.opensearch.name
  endpoints        = ["opensearch-client"]

  depends_on = [juju_integration.opensearch_certificates]
}
