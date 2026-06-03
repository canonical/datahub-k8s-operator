# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

locals {
  module_version = "0.1.0"

  # Deploy the in-module data platform unless external offer URLs are supplied. The variable
  # validation guarantees the three *_offer_url inputs are all set or all empty, so the database
  # one is a sufficient signal.
  deploy_deps = var.database_offer_url == ""

  # Resolve each offer URL: the in-module offer when the data platform is deployed here, otherwise
  # the externally supplied offer URL.
  database_offer   = local.deploy_deps ? module.dependencies[0].offers.database : var.database_offer_url
  kafka_offer      = local.deploy_deps ? module.dependencies[0].offers.kafka_client : var.kafka_offer_url
  opensearch_offer = local.deploy_deps ? module.dependencies[0].offers.opensearch_client : var.opensearch_offer_url

  # Encryption keys: overrides when provided, else generated random values.
  gms_key      = var.encryption_keys.gms_key != "" ? var.encryption_keys.gms_key : random_password.gms_key.result
  frontend_key = var.encryption_keys.frontend_key != "" ? var.encryption_keys.frontend_key : random_password.frontend_key.result

  # DataHub config = user-provided config plus the secret IDs created by this module.
  datahub_config = merge(
    var.datahub.config,
    { "encryption-keys-secret-id" = juju_secret.encryption_keys.secret_id },
    var.oidc != null ? { "oidc-secret-id" = juju_secret.oidc[0].secret_id } : {},
  )
}
