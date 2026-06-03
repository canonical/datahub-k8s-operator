# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

### DATA PLATFORM (machine model): deployed in-module unless external offers are supplied

module "dependencies" {
  count  = local.deploy_deps ? 1 : 0
  source = "./modules/dependencies"

  model_uuid               = var.machine_model_uuid
  postgresql               = var.postgresql
  kafka                    = var.kafka
  zookeeper                = var.zookeeper
  opensearch               = var.opensearch
  self_signed_certificates = var.self_signed_certificates
}

### DATAHUB (K8s model)

module "datahub" {
  source = "../charm"

  app_name    = var.datahub.app_name
  model_uuid  = var.k8s_model_uuid
  channel     = var.datahub.channel
  revision    = var.datahub.revision
  base        = var.datahub.base
  config      = local.datahub_config
  constraints = var.datahub.constraints
  resources   = var.datahub.resources
  units       = var.datahub.units
}

### SECRETS (created and granted to DataHub)

resource "random_password" "gms_key" {
  length  = 32
  special = false
}

resource "random_password" "frontend_key" {
  length  = 32
  special = false
}

resource "juju_secret" "encryption_keys" {
  model_uuid = var.k8s_model_uuid
  name       = "datahub-encryption-keys"
  info       = "Frontend and GMS encryption keys for DataHub."
  value = {
    "gms-key"      = local.gms_key
    "frontend-key" = local.frontend_key
  }
}

resource "juju_secret" "oidc" {
  count      = var.oidc != null ? 1 : 0
  model_uuid = var.k8s_model_uuid
  name       = "datahub-oidc"
  info       = "OIDC client credentials for DataHub SSO."
  value = {
    "client-id"     = var.oidc.client_id
    "client-secret" = var.oidc.client_secret
  }
}

resource "juju_access_secret" "encryption_keys" {
  model_uuid   = var.k8s_model_uuid
  applications = [module.datahub.app_name]
  secret_id    = juju_secret.encryption_keys.secret_id
}

resource "juju_access_secret" "oidc" {
  count        = var.oidc != null ? 1 : 0
  model_uuid   = var.k8s_model_uuid
  applications = [module.datahub.app_name]
  secret_id    = juju_secret.oidc[0].secret_id
}

### INGRESS (K8s model)

resource "juju_application" "traefik_frontend" {
  name       = var.traefik_frontend.app_name
  model_uuid = var.k8s_model_uuid
  trust      = true
  units      = var.traefik_frontend.units
  config     = var.traefik_frontend.config

  charm {
    name     = "traefik-k8s"
    channel  = var.traefik_frontend.channel
    revision = var.traefik_frontend.revision
  }
}

resource "juju_application" "traefik_gms" {
  name       = var.traefik_gms.app_name
  model_uuid = var.k8s_model_uuid
  trust      = true
  units      = var.traefik_gms.units
  config     = var.traefik_gms.config

  charm {
    name     = "traefik-k8s"
    channel  = var.traefik_gms.channel
    revision = var.traefik_gms.revision
  }
}

resource "juju_application" "self_signed_certificates" {
  name       = var.self_signed_certificates.app_name
  model_uuid = var.k8s_model_uuid
  units      = var.self_signed_certificates.units
  config     = var.self_signed_certificates.config

  charm {
    name     = "self-signed-certificates"
    channel  = var.self_signed_certificates.channel
    revision = var.self_signed_certificates.revision
  }
}

### INTEGRATIONS: DataHub to the data platform (cross-model, via offers)

resource "juju_integration" "datahub_database" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.db
  }

  application {
    offer_url = local.database_offer
  }
}

resource "juju_integration" "datahub_kafka" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.kafka
  }

  application {
    offer_url = local.kafka_offer
  }
}

resource "juju_integration" "datahub_opensearch" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.opensearch
  }

  application {
    offer_url = local.opensearch_offer
  }
}

### INTEGRATIONS: DataHub to ingress, and ingress to TLS (in-model)

resource "juju_integration" "datahub_frontend_ingress" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.frontend_ingress
  }

  application {
    name     = juju_application.traefik_frontend.name
    endpoint = "ingress"
  }
}

resource "juju_integration" "datahub_gms_ingress" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.gms_ingress
  }

  application {
    name     = juju_application.traefik_gms.name
    endpoint = "ingress"
  }
}

resource "juju_integration" "traefik_frontend_certificates" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = juju_application.traefik_frontend.name
    endpoint = "certificates"
  }

  application {
    name     = juju_application.self_signed_certificates.name
    endpoint = "certificates"
  }
}

resource "juju_integration" "traefik_gms_certificates" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = juju_application.traefik_gms.name
    endpoint = "certificates"
  }

  application {
    name     = juju_application.self_signed_certificates.name
    endpoint = "certificates"
  }
}

### METADATA

resource "time_static" "deployed_at" {}

resource "time_static" "updated_at" {
  triggers = {
    datahub_channel  = var.datahub.channel
    datahub_revision = tostring(coalesce(var.datahub.revision, 0))
    datahub_config   = jsonencode(var.datahub.config)
  }
}
