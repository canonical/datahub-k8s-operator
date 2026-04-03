# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

# Variables for paths and configuration

PROJECT_ROOT := $(CURDIR)

# Shell strict mode
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

CHARMCRAFT_YAML := $(PROJECT_ROOT)/charmcraft.yaml
IMPORT_SCRIPT := $(PROJECT_ROOT)/scripts/import_rock.sh

REGISTRY := localhost:32000

# Ensure yq is installed: 'sudo snap install yq'
CHARM_NAME := $(shell yq '.name' $(CHARMCRAFT_YAML))
CHARM_BASE := $(shell yq '.bases[0].run-on[0].name' $(CHARMCRAFT_YAML))-$(shell yq '.bases[0].run-on[0].channel' $(CHARMCRAFT_YAML))
CHARM_ARCH := amd64

# --- Rock directories ---
ROCK_DIR := $(PROJECT_ROOT)/datahub_rocks

ACTIONS_DIR := $(ROCK_DIR)/actions
ACTIONS_ROCKCRAFT_YAML := $(ACTIONS_DIR)/rockcraft.yaml
ACTIONS_NAME := $(shell yq '.name' $(ACTIONS_ROCKCRAFT_YAML))
ACTIONS_VERSION := $(shell yq '.version' $(ACTIONS_ROCKCRAFT_YAML))
ACTIONS_ROCK := $(ACTIONS_DIR)/$(ACTIONS_NAME)_$(ACTIONS_VERSION)_$(CHARM_ARCH).rock

FRONTEND_DIR := $(ROCK_DIR)/frontend
FRONTEND_ROCKCRAFT_YAML := $(FRONTEND_DIR)/rockcraft.yaml
FRONTEND_NAME := $(shell yq '.name' $(FRONTEND_ROCKCRAFT_YAML))
FRONTEND_VERSION := $(shell yq '.version' $(FRONTEND_ROCKCRAFT_YAML))
FRONTEND_ROCK := $(FRONTEND_DIR)/$(FRONTEND_NAME)_$(FRONTEND_VERSION)_$(CHARM_ARCH).rock

GMS_DIR := $(ROCK_DIR)/gms
GMS_ROCKCRAFT_YAML := $(GMS_DIR)/rockcraft.yaml
GMS_NAME := $(shell yq '.name' $(GMS_ROCKCRAFT_YAML))
GMS_VERSION := $(shell yq '.version' $(GMS_ROCKCRAFT_YAML))
GMS_ROCK := $(GMS_DIR)/$(GMS_NAME)_$(GMS_VERSION)_$(CHARM_ARCH).rock

# The expected output file from charmcraft pack
CHARM_FILE := $(PROJECT_ROOT)/$(CHARM_NAME)_$(CHARM_BASE)-$(CHARM_ARCH).charm

# Default target
.PHONY: all
all: build

.PHONY: help
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  build                Build charm and all rocks"
	@echo "  build-charm          Build the charm using charmcraft"
	@echo "  build-rock           Build all rocks"
	@echo "  build-rock-actions   Build the actions rock"
	@echo "  build-rock-frontend  Build the frontend rock"
	@echo "  build-rock-gms       Build the GMS rock"
	@echo "  check-build-deps     Check if build dependencies are installed"
	@echo "  check-deploy-deps    Check if deployment dependencies are installed"
	@echo "  check-deps           Check if all dependencies are installed"
	@echo "  checks               Run all code quality checks"
	@echo "  clean                Remove built charm and rock files"
	@echo "  clean-charmcraft     Clean charmcraft environment"
	@echo "  clean-rockcraft      Clean all rockcraft environments"
	@echo "  create-secret        Create a Juju secret with random encryption keys"
	@echo "  deploy-local         Deploy charm with local resources using image digests (requires SECRET_ID=<id>)"
	@echo "  fmt                  Apply coding style standards to code"
	@echo "  import-rock          Build and import all rocks into MicroK8s"
	@echo "  import-rock-actions  Build and import the actions rock into MicroK8s"
	@echo "  import-rock-frontend Build and import the frontend rock into MicroK8s"
	@echo "  import-rock-gms      Build and import the GMS rock into MicroK8s"
	@echo "  lint                 Check code against coding style standards"
	@echo "  test                 Run unit and static tests"
	@echo "  test-integration     Run integration tests"
	@echo "  test-static          Run static analysis tests"
	@echo "  test-unit            Run unit tests"
	@echo "  help                 Show this help message"
	@echo "  venv                 Create a virtual environment"

.PHONY: build
build: build-charm build-rock

# --- Dependency checks ---

.PHONY: check-build-deps
check-build-deps:
	@which yq >/dev/null || (echo "yq not found" && exit 1)
	@which charmcraft >/dev/null || (echo "charmcraft not found" && exit 1)
	@which rockcraft >/dev/null || (echo "rockcraft not found" && exit 1)
	@which tox >/dev/null || (echo "tox not found" && exit 1)
	@echo "All build dependencies are installed."

.PHONY: check-deploy-deps
check-deploy-deps:
	@which juju >/dev/null || (echo "juju not found" && exit 1)
	@which docker >/dev/null || (echo "docker not found" && exit 1)
	@which microk8s >/dev/null || (echo "microk8s not found" && exit 1)
	@which skopeo >/dev/null || (echo "skopeo not found" && exit 1)
	@echo "All deployment dependencies are installed."

.PHONY: check-deps
check-deps: check-build-deps check-deploy-deps

# --- Code quality ---

.PHONY: checks
checks: fmt lint test

.PHONY: fmt
fmt:
	tox -e fmt

.PHONY: lint
lint:
	tox -e lint

# --- Tests ---

.PHONY: test
test: test-unit test-static

.PHONY: test-integration
test-integration:
	tox -e integration

.PHONY: test-static
test-static:
	tox -e static

.PHONY: test-unit
test-unit:
	tox -e unit

# --- Charm ---

.PHONY: build-charm
build-charm:
	@echo "Building charm..."
	cd $(PROJECT_ROOT) && charmcraft pack --use-lxd --verbose

# --- Rocks ---

# Build each rock only if rockcraft.yaml changes or the file is missing
$(ACTIONS_ROCK): $(ACTIONS_ROCKCRAFT_YAML)
	@echo "Building actions rock..."
	cd $(ACTIONS_DIR) && rockcraft pack --use-lxd --verbose

$(FRONTEND_ROCK): $(FRONTEND_ROCKCRAFT_YAML)
	@echo "Building frontend rock..."
	cd $(FRONTEND_DIR) && rockcraft pack --use-lxd --verbose

$(GMS_ROCK): $(GMS_ROCKCRAFT_YAML)
	@echo "Building GMS rock..."
	cd $(GMS_DIR) && rockcraft pack --use-lxd --verbose

.PHONY: build-rock-actions
build-rock-actions: $(ACTIONS_ROCK)

.PHONY: build-rock-frontend
build-rock-frontend: $(FRONTEND_ROCK)

.PHONY: build-rock-gms
build-rock-gms: $(GMS_ROCK)

.PHONY: build-rock
build-rock: build-rock-actions build-rock-frontend build-rock-gms

# --- Import rocks ---

.PHONY: import-rock-actions
import-rock-actions: $(ACTIONS_ROCK)
	@echo "Importing actions rock $(ACTIONS_ROCK)..."
	$(IMPORT_SCRIPT) $(ACTIONS_ROCK) $(ACTIONS_NAME) $(ACTIONS_VERSION) --latest

.PHONY: import-rock-frontend
import-rock-frontend: $(FRONTEND_ROCK)
	@echo "Importing frontend rock $(FRONTEND_ROCK)..."
	$(IMPORT_SCRIPT) $(FRONTEND_ROCK) $(FRONTEND_NAME) $(FRONTEND_VERSION) --latest

.PHONY: import-rock-gms
import-rock-gms: $(GMS_ROCK)
	@echo "Importing GMS rock $(GMS_ROCK)..."
	$(IMPORT_SCRIPT) $(GMS_ROCK) $(GMS_NAME) $(GMS_VERSION) --latest

.PHONY: import-rock
import-rock: import-rock-actions import-rock-frontend import-rock-gms

# --- Deploy ---

SECRET_NAME ?= datahub-encryption-keys

.PHONY: create-secret
create-secret:
	@GMS_KEY=$$(openssl rand -base64 32) && \
	FE_KEY=$$(openssl rand -base64 32) && \
	SECRET_FILE=$$(mktemp -p "$$HOME" datahub-secret.XXXXXX.yaml) && \
	echo "gms-key: $$GMS_KEY" > "$$SECRET_FILE" && \
	echo "frontend-key: $$FE_KEY" >> "$$SECRET_FILE" && \
	SECRET_ID=$$(juju add-secret $(SECRET_NAME) --file="$$SECRET_FILE") && \
	rm -f "$$SECRET_FILE" && \
	echo "Secret created: $$SECRET_ID" && \
	echo "Grant it after deploy with: juju grant-secret $(SECRET_NAME) $(CHARM_NAME)"

.PHONY: deploy-local
deploy-local:
ifndef SECRET_ID
	$(error SECRET_ID is required. Usage: make deploy-local SECRET_ID=<secret-id>)
endif
	@echo "Fetching image digests..."
	@ACTIONS_DIGEST=$$(skopeo inspect --tls-verify=false docker://$(REGISTRY)/$(ACTIONS_NAME):latest 2>/dev/null | jq -r '.Digest' || echo "latest") && \
	FRONTEND_DIGEST=$$(skopeo inspect --tls-verify=false docker://$(REGISTRY)/$(FRONTEND_NAME):latest 2>/dev/null | jq -r '.Digest' || echo "latest") && \
	GMS_DIGEST=$$(skopeo inspect --tls-verify=false docker://$(REGISTRY)/$(GMS_NAME):latest 2>/dev/null | jq -r '.Digest' || echo "latest") && \
	echo "Deploying charm with local resources (using digests)..." && \
	juju deploy $(CHARM_FILE) \
		--resource datahub-actions=$(REGISTRY)/$(ACTIONS_NAME)@$$ACTIONS_DIGEST \
		--resource datahub-frontend=$(REGISTRY)/$(FRONTEND_NAME)@$$FRONTEND_DIGEST \
		--resource datahub-gms=$(REGISTRY)/$(GMS_NAME)@$$GMS_DIGEST \
		--config encryption-keys-secret-id=$(SECRET_ID)

# --- Clean ---

.PHONY: clean
clean:
	@echo "Cleaning up..."
	rm -f $(PROJECT_ROOT)/*.charm
	rm -f $(ACTIONS_DIR)/*.rock
	rm -f $(FRONTEND_DIR)/*.rock
	rm -f $(GMS_DIR)/*.rock

.PHONY: clean-charmcraft
clean-charmcraft:
	@echo "Cleaning charmcraft environment..."
	cd $(PROJECT_ROOT) && charmcraft clean

.PHONY: clean-rockcraft
clean-rockcraft:
	@echo "Cleaning all rockcraft environments..."
	cd $(ACTIONS_DIR) && rockcraft clean
	cd $(FRONTEND_DIR) && rockcraft clean
	cd $(GMS_DIR) && rockcraft clean

# --- Virtual environment ---

.PHONY: venv
venv:
	tox devenv -e integration
