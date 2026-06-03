# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "metadata" {
  description = "Metadata of the product deployment."
  value = {
    version     = local.module_version
    deployed_at = time_static.deployed_at.rfc3339
    updated_at  = time_static.updated_at.rfc3339
  }
}

output "models" {
  description = "Map of the deployed models and the applications in each."
  value = {
    datahub = {
      model_uuid = var.k8s_model_uuid
      components = {
        datahub-k8s              = module.datahub.app_name
        traefik-frontend         = juju_application.traefik_frontend.name
        traefik-gms              = juju_application.traefik_gms.name
        self-signed-certificates = juju_application.self_signed_certificates.name
      }
    }
    data-platform = {
      model_uuid = local.deploy_deps ? var.machine_model_uuid : null
      components = local.deploy_deps ? module.dependencies[0].app_names : {}
    }
  }
}

output "offers" {
  description = "Data-platform offer URLs consumed by DataHub (in-module or externally provided)."
  value = {
    database          = local.database_offer
    kafka_client      = local.kafka_offer
    opensearch_client = local.opensearch_offer
  }
}
