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

### INGRESS + TLS (K8s model)

module "traefik_frontend" {
  source = "./modules/traefik-k8s"

  model_uuid = var.k8s_model_uuid
  app_name   = var.traefik_frontend.app_name
  channel    = var.traefik_frontend.channel
  revision   = var.traefik_frontend.revision
  config     = var.traefik_frontend.config
  units      = var.traefik_frontend.units
  trust      = true
}

module "traefik_gms" {
  source = "./modules/traefik-k8s"

  model_uuid = var.k8s_model_uuid
  app_name   = var.traefik_gms.app_name
  channel    = var.traefik_gms.channel
  revision   = var.traefik_gms.revision
  config     = var.traefik_gms.config
  units      = var.traefik_gms.units
  trust      = true
}

module "self_signed_certificates" {
  source = "./modules/self-signed-certificates"

  model_uuid  = var.k8s_model_uuid
  app_name    = var.self_signed_certificates.app_name
  channel     = var.self_signed_certificates.channel
  revision    = var.self_signed_certificates.revision
  base        = var.self_signed_certificates.base
  constraints = var.self_signed_certificates.constraints
  config      = var.self_signed_certificates.config
  units       = var.self_signed_certificates.units
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
    name     = module.traefik_frontend.provides.ingress.name
    endpoint = module.traefik_frontend.provides.ingress.endpoint
  }
}

resource "juju_integration" "datahub_gms_ingress" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.gms_ingress
  }

  application {
    name     = module.traefik_gms.provides.ingress.name
    endpoint = module.traefik_gms.provides.ingress.endpoint
  }
}

resource "juju_integration" "traefik_frontend_certificates" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.traefik_frontend.requires.certificates.name
    endpoint = module.traefik_frontend.requires.certificates.endpoint
  }

  application {
    name     = module.self_signed_certificates.provides.certificates.name
    endpoint = module.self_signed_certificates.provides.certificates.endpoint
  }
}

resource "juju_integration" "traefik_gms_certificates" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.traefik_gms.requires.certificates.name
    endpoint = module.traefik_gms.requires.certificates.endpoint
  }

  application {
    name     = module.self_signed_certificates.provides.certificates.name
    endpoint = module.self_signed_certificates.provides.certificates.endpoint
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
