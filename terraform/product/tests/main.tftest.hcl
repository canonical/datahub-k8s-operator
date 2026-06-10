# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

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
