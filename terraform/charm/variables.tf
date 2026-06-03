# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "datahub-k8s"
}

variable "base" {
  description = "The operating system on which to deploy."
  type        = string
  default     = "ubuntu@22.04"
}

variable "channel" {
  description = "The channel to use when deploying the charm."
  type        = string
  default     = "latest/edge"
}

variable "config" {
  description = "Application configuration. Options at https://charmhub.io/datahub-k8s/configurations."
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = null
}

variable "model_uuid" {
  description = "Reference to the `juju_model` UUID to deploy to."
  type        = string
}

variable "resources" {
  description = "Map of OCI-image resources (datahub-actions, datahub-frontend, datahub-gms) to use instead of the charm's bundled images."
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Revision number of the charm to deploy. Null deploys the latest revision on the channel."
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy."
  type        = number
  default     = 1
}
