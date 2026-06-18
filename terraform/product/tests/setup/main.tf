# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

terraform {
  required_version = "~> 1.14"
  required_providers {
    juju = {
      version = "~> 2.0"
      source  = "juju/juju"
    }
  }
}

provider "juju" {}

resource "juju_model" "machine" {
  name = "tf-testing-deps-${formatdate("YYYYMMDDhhmmss", timestamp())}"
}

resource "juju_model" "k8s" {
  name       = "tf-testing-dh-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  credential = var.k8s_credential_name

  cloud {
    name = var.k8s_cloud_name
  }

  config = var.k8s_workload_storage != "" ? { workload-storage = var.k8s_workload_storage } : {}
}

variable "k8s_workload_storage" {
  description = "StorageClass for workloads in the K8s model. Empty uses the cloud default."
  type        = string
  default     = ""
}

variable "k8s_cloud_name" {
  description = "Name of the Kubernetes cloud registered on the controller."
  type        = string
  default     = "tfk8s"
}

variable "k8s_credential_name" {
  description = "Name of the credential for the Kubernetes cloud."
  type        = string
  default     = "tfk8s"
}

output "machine_model_uuid" {
  value = juju_model.machine.uuid
}

output "k8s_model_uuid" {
  value = juju_model.k8s.uuid
}
