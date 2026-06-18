# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Defaults match CI (operator-workflows registers the K8s cloud as `tfk8s`, no
# workload-storage override). For local runs pass globals via environment, e.g.:
#   TF_VAR_k8s_cloud_name=microk8s TF_VAR_k8s_credential_name=microk8s \
#   TF_VAR_k8s_workload_storage=microk8s-hostpath terraform test
run "setup_tests" {
  module {
    source = "./tests/setup"
  }
}

run "full_deploy" {
  variables {
    k8s_model_uuid     = run.setup_tests.k8s_model_uuid
    machine_model_uuid = run.setup_tests.machine_model_uuid
  }

  assert {
    condition     = output.models.datahub.components["datahub-k8s"] == "datahub-k8s"
    error_message = "datahub-k8s application not present in the datahub model components"
  }

  assert {
    condition     = output.offers.database != "" && output.offers.kafka_client != "" && output.offers.opensearch_client != ""
    error_message = "data-platform offer URLs were not produced"
  }
}

# OpenSearch is the long wait on the machine cloud; it only reaches active with the kernel sysctls
# set by tests/setup/pre_run_script.sh (wired via the workflow's additional-setup-script).
run "wait_for_opensearch_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.machine_model_uuid
    app_name   = "opensearch"
    timeout    = 1200
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "opensearch did not reach active state"
  }
}

run "wait_for_datahub_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.k8s_model_uuid
    app_name   = "datahub-k8s"
    timeout    = 1800
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "datahub-k8s did not reach active state"
  }
}

# Enable SSO on the now-active stack: deploys the external IdP integrator and the oauth
# integration on top of the base deploy. self-signed-certificates already gives the frontend
# ingress HTTPS, so the charm's OIDC guard is satisfied.
#
# TODO: Once datahub-k8s with `oauth` is in latest/edge, delete the `command = plan` line
# and uncomment the two wait runs that follow.
run "enable_sso" {
  command = plan

  variables {
    k8s_model_uuid     = run.setup_tests.k8s_model_uuid
    machine_model_uuid = run.setup_tests.machine_model_uuid
    oauth_external_idp_integrator_config = {
      client_id     = "stub-client-id"
      client_secret = "stub-client-secret"
    }
  }

  assert {
    condition     = output.models.datahub.components["oauth-external-idp-integrator"] == "oauth-external-idp-integrator"
    error_message = "oauth-external-idp-integrator not present in the datahub model components when SSO is enabled"
  }
}

# run "wait_for_integrator_active" {
#   module {
#     source = "./tests/wait_for_active"
#   }
#
#   variables {
#     model_uuid = run.setup_tests.k8s_model_uuid
#     app_name   = "oauth-external-idp-integrator"
#     timeout    = 600
#   }
#
#   assert {
#     condition     = data.external.app_status.result.status == "active"
#     error_message = "oauth-external-idp-integrator did not reach active state"
#   }
# }
#
# # DataHub must stay active once the oauth relation is wired (HTTPS guard satisfied, client
# # config published, provider info rendered).
# run "wait_for_datahub_active_with_sso" {
#   module {
#     source = "./tests/wait_for_active"
#   }
#
#   variables {
#     model_uuid = run.setup_tests.k8s_model_uuid
#     app_name   = "datahub-k8s"
#     timeout    = 1800
#   }
#
#   assert {
#     condition     = data.external.app_status.result.status == "active"
#     error_message = "datahub-k8s did not stay active after enabling SSO"
#   }
# }
