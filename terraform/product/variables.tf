# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

variable "database_offer_url" {
  description = <<-EOT
    Offer URL for an external PostgreSQL `database` endpoint. Leave empty to deploy PostgreSQL
    in-module (local, single-controller). Set together with kafka_offer_url and
    opensearch_offer_url for the prod two-controller split (data platform deployed separately).
  EOT
  type        = string
  default     = ""

  validation {
    condition = (
      (var.database_offer_url == "" && var.kafka_offer_url == "" && var.opensearch_offer_url == "") ||
      (var.database_offer_url != "" && var.kafka_offer_url != "" && var.opensearch_offer_url != "")
    )
    error_message = "Set all of database_offer_url, kafka_offer_url and opensearch_offer_url (external data platform), or none (deploy the data platform in-module)."
  }
}

variable "datahub" {
  description = "Configuration for the datahub-k8s charm."
  type = object({
    app_name    = optional(string, "datahub-k8s")
    channel     = optional(string, "latest/edge")
    revision    = optional(number)
    base        = optional(string, "ubuntu@22.04")
    constraints = optional(string)
    config      = optional(map(string), {})
    resources   = optional(map(string), {})
    units       = optional(number, 1)
  })
  default = {}
}

variable "encryption_keys" {
  description = <<-EOT
    Optional overrides for the DataHub encryption keys. When a field is empty a random value is
    generated. These are internal encryption keys, not credentials. Leave empty unless matching
    an existing deployment.
  EOT
  type = object({
    gms_key      = optional(string, "")
    frontend_key = optional(string, "")
  })
  default = {}
}

variable "k8s_model_uuid" {
  description = "UUID of the Kubernetes Juju model where DataHub and the ingress are deployed."
  type        = string
}

variable "kafka" {
  description = "Configuration for the in-module Kafka charm (used when kafka_offer_url is empty)."
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

variable "kafka_offer_url" {
  description = "Offer URL for an external Kafka `kafka-client` endpoint. See database_offer_url."
  type        = string
  default     = ""
}

variable "machine_model_uuid" {
  description = "UUID of the machine Juju model for the in-module data platform. Required when the *_offer_url inputs are empty."
  type        = string
  default     = ""

  validation {
    condition     = var.database_offer_url != "" || var.machine_model_uuid != ""
    error_message = "machine_model_uuid is required when deploying the in-module data platform (i.e. when the *_offer_url inputs are empty)."
  }
}

variable "oidc" {
  description = "OIDC client credentials for SSO. When set, a Juju secret is created and granted to DataHub. Leave null to disable SSO."
  type = object({
    client_id     = string
    client_secret = string
  })
  default   = null
  sensitive = true
}

variable "opensearch" {
  description = "Configuration for the in-module OpenSearch charm (used when opensearch_offer_url is empty)."
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

variable "opensearch_offer_url" {
  description = "Offer URL for an external OpenSearch `opensearch-client` endpoint. See database_offer_url."
  type        = string
  default     = ""
}

variable "postgresql" {
  description = "Configuration for the in-module PostgreSQL charm (used when database_offer_url is empty)."
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
  description = "Configuration for the self-signed-certificates charm (TLS for OpenSearch and the Traefik ingresses)."
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

variable "traefik_frontend" {
  description = "Configuration for the Traefik ingress in front of the DataHub frontend."
  type = object({
    app_name = optional(string, "traefik-frontend")
    channel  = optional(string, "latest/stable")
    revision = optional(number)
    config   = optional(map(string), {})
    units    = optional(number, 1)
  })
  default = {}
}

variable "traefik_gms" {
  description = "Configuration for the Traefik ingress in front of DataHub GMS."
  type = object({
    app_name = optional(string, "traefik-gms")
    channel  = optional(string, "latest/stable")
    revision = optional(number)
    config   = optional(map(string), {})
    units    = optional(number, 1)
  })
  default = {}
}

variable "zookeeper" {
  description = "Configuration for the in-module ZooKeeper charm (backend for Kafka; used when kafka_offer_url is empty)."
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
