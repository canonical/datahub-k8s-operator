# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

### DATA PLATFORM (data-platform K8s model): deployed in-module unless external offers are supplied

module "dependencies" {
  count  = local.deploy_deps ? 1 : 0
  source = "./modules/dependencies"

  model_uuid               = var.data_platform_model_uuid
  postgresql               = var.postgresql
  opensearch               = var.opensearch
  self_signed_certificates = var.self_signed_certificates
}

# Kafka sits in the DataHub model rather than with the rest of the data platform: kafka-k8s cannot
# currently serve its `kafka-client` endpoint over a cross-model relation. It is still gated on
# `deploy_deps`, so supplying `kafka_offer_url` skips it instead of deploying an application the
# integration below would never use.
module "kafka" {
  count  = local.deploy_deps ? 1 : 0
  source = "git::https://github.com/canonical/kafka-k8s-bundle//terraform?ref=0456d03"

  model_uuid = var.k8s_model_uuid
  broker     = var.kafka_broker
  controller = var.kafka_controller
  connect    = var.kafka_connect
  karapace   = var.kafka_karapace
  ui         = var.kafka_ui
  profile    = var.kafka_profile
}

### DATAHUB (K8s model)

module "datahub" {
  source = "../../charm"

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

resource "juju_access_secret" "encryption_keys" {
  model_uuid   = var.k8s_model_uuid
  applications = [module.datahub.app_name]
  secret_id    = juju_secret.encryption_keys.secret_id
}

### SSO (optional): external IdP integrator related to DataHub over the `oauth` interface

module "oauth_external_idp_integrator" {
  count  = local.enable_sso ? 1 : 0
  source = "./modules/oauth-external-idp-integrator"

  app_name    = var.oauth_external_idp_integrator_charm.app_name
  model_uuid  = var.k8s_model_uuid
  channel     = var.oauth_external_idp_integrator_charm.channel
  revision    = var.oauth_external_idp_integrator_charm.revision
  base        = var.oauth_external_idp_integrator_charm.base
  constraints = var.oauth_external_idp_integrator_charm.constraints
  units       = var.oauth_external_idp_integrator_charm.units

  config = {
    issuer_url             = var.oauth_external_idp_integrator_config.issuer_url
    authorization_endpoint = var.oauth_external_idp_integrator_config.authorization_endpoint
    token_endpoint         = var.oauth_external_idp_integrator_config.token_endpoint
    introspection_endpoint = var.oauth_external_idp_integrator_config.introspection_endpoint
    userinfo_endpoint      = var.oauth_external_idp_integrator_config.userinfo_endpoint
    jwks_endpoint          = var.oauth_external_idp_integrator_config.jwks_endpoint
    scope                  = var.oauth_external_idp_integrator_config.scope
    client_id              = sensitive(var.oauth_external_idp_integrator_config.client_id)
    client_secret          = sensitive(var.oauth_external_idp_integrator_config.client_secret)
    jwt_access_token       = tostring(var.oauth_external_idp_integrator_config.jwt_access_token)
  }
}

resource "juju_integration" "datahub_oauth" {
  count      = local.enable_sso ? 1 : 0
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.datahub.app_name
    endpoint = module.datahub.requires.oauth
  }

  application {
    name     = module.oauth_external_idp_integrator[0].provides.oauth.name
    endpoint = module.oauth_external_idp_integrator[0].provides.oauth.endpoint
  }
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
  source = "git::https://github.com/canonical/self-signed-certificates-operator//terraform?ref=e7527a7"

  model_uuid  = var.k8s_model_uuid
  app_name    = var.self_signed_certificates.app_name
  channel     = var.self_signed_certificates.channel
  revision    = var.self_signed_certificates.revision
  base        = var.self_signed_certificates.base
  constraints = var.self_signed_certificates.constraints
  config      = var.self_signed_certificates.config
  units       = var.self_signed_certificates.units
}

### INTEGRATIONS: DataHub to the data platform (via offers, except in-model Kafka)

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

  # In-module Kafka shares the DataHub model, so it is related directly; only an externally
  # supplied Kafka is consumed over an offer.
  application {
    name      = local.deploy_deps ? module.kafka[0].app_names.broker : null
    endpoint  = local.deploy_deps ? "kafka-client" : null
    offer_url = local.deploy_deps ? null : var.kafka_offer_url
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

# Kafka 4 runs in KRaft mode and stays in "waiting for internal TLS setup" until the traffic
# between its broker and controller roles is secured, so the peer endpoint gets a certificates
# provider. The client `certificates` endpoint is deliberately left unrelated: the kafka-client
# listener then stays plaintext and DataHub needs no CA trust.
resource "juju_integration" "kafka_peer_certificates" {
  count      = local.deploy_deps ? 1 : 0
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.kafka[0].app_names.broker
    endpoint = "peer-certificates"
  }

  application {
    name     = module.self_signed_certificates.app_name
    endpoint = module.self_signed_certificates.provides.certificates
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
    name     = module.self_signed_certificates.app_name
    endpoint = module.self_signed_certificates.provides.certificates
  }
}

resource "juju_integration" "traefik_gms_certificates" {
  model_uuid = var.k8s_model_uuid

  application {
    name     = module.traefik_gms.requires.certificates.name
    endpoint = module.traefik_gms.requires.certificates.endpoint
  }

  application {
    name     = module.self_signed_certificates.app_name
    endpoint = module.self_signed_certificates.provides.certificates
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
