# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

run "setup_tests" {
  module {
    source = "./tests/setup"
  }
}

run "basic_deploy" {
  variables {
    model_uuid = run.setup_tests.model_uuid
    channel    = "latest/edge"
  }

  assert {
    condition     = output.app_name == "datahub-k8s"
    error_message = "datahub-k8s app_name did not match expected"
  }

  assert {
    condition     = output.requires.frontend_ingress == "frontend-ingress"
    error_message = "requires.frontend_ingress endpoint did not match expected"
  }
}
