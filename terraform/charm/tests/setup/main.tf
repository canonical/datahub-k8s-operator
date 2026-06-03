# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

terraform {
  required_version = "~> 1.12"
  required_providers {
    juju = {
      version = "~> 1.0"
      source  = "juju/juju"
    }
  }
}

provider "juju" {}

variable "k8s_cloud_name" {
  description = "Name of the Kubernetes cloud registered on the controller."
  type        = string
  default     = "microk8s"
}

variable "k8s_credential_name" {
  description = "Name of the credential for the Kubernetes cloud."
  type        = string
  default     = "microk8s"
}

variable "workload_storage_class" {
  description = "StorageClass to use for workloads in the Kubernetes model."
  type        = string
  default     = "microk8s-hostpath"
}

resource "juju_model" "test_model" {
  name       = "tf-testing-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  credential = var.k8s_credential_name

  cloud {
    name = var.k8s_cloud_name
  }

  config = {
    workload-storage = var.workload_storage_class
  }
}

output "model_uuid" {
  value = juju_model.test_model.uuid
}
