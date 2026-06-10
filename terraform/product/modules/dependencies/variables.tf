# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

variable "model_uuid" {
  description = "UUID of the (machine) Juju model to deploy the data platform into."
  type        = string
}

variable "kafka" {
  description = "Configuration for the Kafka charm."
  type = object({
    app_name           = optional(string, "kafka")
    channel            = optional(string, "3/stable")
    revision           = optional(number)
    base               = optional(string, "ubuntu@22.04")
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    storage_directives = optional(map(string), {})
    units              = optional(number, 1)
  })
  default = {}
}

variable "opensearch" {
  description = "Configuration for the OpenSearch charm."
  type = object({
    app_name           = optional(string, "opensearch")
    channel            = optional(string, "2/stable")
    revision           = optional(number)
    base               = optional(string, "ubuntu@22.04")
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    storage_directives = optional(map(string), {})
    units              = optional(number, 2)
  })
  default = {}
}

variable "postgresql" {
  description = "Configuration for the PostgreSQL charm."
  type = object({
    app_name           = optional(string, "postgresql")
    channel            = optional(string, "14/stable")
    revision           = optional(number)
    base               = optional(string, "ubuntu@22.04")
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    storage_directives = optional(map(string), {})
    units              = optional(number, 1)
  })
  default = {}
}

variable "self_signed_certificates" {
  description = "Configuration for the self-signed-certificates charm (TLS for OpenSearch)."
  type = object({
    app_name    = optional(string, "self-signed-certificates")
    channel     = optional(string, "latest/stable")
    revision    = optional(number)
    base        = optional(string, "ubuntu@22.04")
    constraints = optional(string, "arch=amd64")
    config      = optional(map(string), {})
    units       = optional(number, 1)
  })
  default = {}
}

variable "zookeeper" {
  description = "Configuration for the ZooKeeper charm (backend for Kafka)."
  type = object({
    app_name           = optional(string, "zookeeper")
    channel            = optional(string, "3/stable")
    revision           = optional(number)
    base               = optional(string, "ubuntu@22.04")
    constraints        = optional(string, "arch=amd64")
    config             = optional(map(string), {})
    storage_directives = optional(map(string), {})
    units              = optional(number, 1)
  })
  default = {}
}
