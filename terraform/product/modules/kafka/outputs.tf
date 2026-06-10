# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed application."
  value       = juju_application.kafka
}

output "provides" {
  description = "Map of all the provided endpoints."
  value = {
    kafka_client = {
      name     = juju_application.kafka.name
      endpoint = "kafka-client"
    }
  }
}

output "requires" {
  description = "Map of all the required endpoints."
  value = {
    zookeeper = {
      name     = juju_application.kafka.name
      endpoint = "zookeeper"
    }
  }
}
