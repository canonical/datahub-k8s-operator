# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name to give the deployed application."
  type        = string
  default     = "traefik-k8s"
  nullable    = false
}

variable "channel" {
  description = "Channel of the charm."
  type        = string
  default     = "latest/stable"
  nullable    = false
}

variable "config" {
  description = "Map for configuration options."
  type        = map(string)
  default     = {}
}

variable "model_uuid" {
  description = "Reference to an existing model uuid."
  type        = string
  nullable    = false
}

variable "revision" {
  description = "Revision number of the charm."
  type        = number
  default     = null
}

variable "trust" {
  description = "Whether to grant the application access to the Kubernetes cluster (required for the load balancer)."
  type        = bool
  default     = false
}

variable "units" {
  description = "Unit count/scale."
  type        = number
  default     = 1
}
