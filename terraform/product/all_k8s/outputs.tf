# Copyright 2024 Canonical Ltd.
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
      components = merge(
        {
          datahub-k8s              = module.datahub.app_name
          traefik-frontend         = module.traefik_frontend.application.name
          traefik-gms              = module.traefik_gms.application.name
          self-signed-certificates = module.self_signed_certificates.app_name
        },
        # Kafka lives in this model, not with the rest of the data platform. The bundle reports one
        # name per role and nulls the roles it did not deploy; in single mode broker and controller
        # are the same application.
        local.deploy_deps ? {
          for role, name in module.kafka[0].app_names : "kafka-${role}" => name if name != null
        } : {},
        local.enable_sso ? {
          oauth-external-idp-integrator = module.oauth_external_idp_integrator[0].application.name
        } : {},
      )
    }
    data-platform = {
      model_uuid = local.deploy_deps ? var.data_platform_model_uuid : null
      components = local.deploy_deps ? module.dependencies[0].components : {}
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
