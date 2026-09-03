# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Application name in the Juju model"
  type        = string
  default     = "opensearch-k8s"
}

variable "channel" {
  description = "Charm channel to deploy from (e.g. 2/stable, 2/edge)"
  type        = string
  default     = "2/edge"
}

variable "base" {
  description = "Base OS version to deploy from"
  type        = string
  default     = "ubuntu@24.04"
}

variable "config" {
  description = "Application configuration key-value map"
  type        = map(string)
  default     = {}
}

variable "model_uuid" {
  description = "Reference to the Juju model where the application will be deployed"
  type        = string
}

variable "resources" {
  description = "Map of charm resource names to their revision numbers or OCI image paths"
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Specific charm revision to deploy"
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy"
  type        = number
  default     = 1
}

variable "storage_directives" {
  description = "Storage constraint definition map (e.g., { opensearch-data = \"20G\" })"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Constraints to apply when deploying the application"
  type        = string
  default     = null
}