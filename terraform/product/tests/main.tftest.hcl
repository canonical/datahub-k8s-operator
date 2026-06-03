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
