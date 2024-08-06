# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

You can create an environment for development with `tox`:

```shell
tox devenv -e integration
source venv/bin/activate
```

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox run -e format        # update your code according to linting rules
tox run -e lint          # code style
tox run -e static        # static type checking
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'format', 'lint', 'static', and 'unit' environments
```

## Recommended development environment setup

The recommended development environment is set up on `multipass`.

Get it via:

```shell
sudo snap install multipass --classic
```

Steps to setting up the environment:
```shell
multipass launch -c 4 -m 16G -d 50G -n charm-dev charm-dev
multipass shell charm-dev
# Refer to [1] below for a note.
juju switch lxd
juju add-model datahub-vm lxd
juju switch microk8s
juju add-model datahub-k8s microk8s
# Refer to [2] below for a note.
```

[1]: Minor and/or patch versions of the tech stack can introduce regressions, if you are having problems use the following commands to switch to tested channels:
```shell
sudo snap switch juju --channel 3.4/stable
sudo snap refresh juju
sudo snap switch microk8s --channel 1.29/stable
sudo snap refresh microk8s --classic
```

Afterwards, you will have to recreate the controllers and models from scratch:
```shell
juju bootstrap lxd lxd
juju add-model datahub-vm
juju bootstrap microk8s microk8s
juju add-model datahub-k8s microk8s
```

[2]: DataHub has Opensearch as a dependency. In order to deploy the Opensearch charm, you need to edit certain kernel parameters inside your `multipass` instance as instructed [here](https://charmhub.io/opensearch/docs/t-set-up#heading--kernel-parameters).

## Deploy the dependencies

DataHub has PostgreSQL, Kafka, and OpenSearch as dependencies. Opensearch only has a machine charm, while the others have both a machine and a Kubernetes charm. In production, all of the dependencies are intended to be deployed on machines but for simplification of development purposes you can have only Opensearch on a machine and deploy the rest on `microk8s`. So, the instructions here will cover that scenario.

Deploying Opensearch:
```shell
juju switch lxd:datahub-vm
juju deploy opensearch --channel 2/edge -n 2
juju deploy self-signed-certificates
juju integrate opensearch self-signed-certificates
juju offer opensearch:opensearch-client os-client
```

Deploying other dependencies:
```shell
juju switch microk8s:datahub-k8s
juju deploy postgresql-k8s
juju deploy kafka-k8s
juju deploy zookeeper-k8s
juju integrate kafka-k8s zookeeper-k8s
juju consume lxd:admin/datahub-vm.os-client
```

## Build the charm

Build the charm in this git repository using:
```shell
charmcraft pack
```

## Deploy the charm

Deploy the charm after the build step with the following command:
```shell
juju switch microk8s:datahub-k8s
cd /path/to/charm/dir
juju deploy ./datahub-k8s_ubuntu-22.04-amd64.charm \
--resource datahub-actions=acryldata/datahub-actions:v0.0.15 \
--resource datahub-frontend=acryldata/datahub-frontend-react:v0.13.3 \
--resource datahub-gms=acryldata/datahub-gms:v0.13.3 \
--resource datahub-mae-consumer=acryldata/datahub-mae-consumer:v0.13.3 \
--resource datahub-mce-consumer=acryldata/datahub-mce-consumer:v0.13.3 \
--resource datahub-opensearch-setup=acryldata/datahub-elasticsearch-setup:v0.13.3 \
--resource datahub-kafka-setup=acryldata/datahub-kafka-setup:v0.13.3 \
--resource datahub-postgresql-setup=acryldata/datahub-postgres-setup:v0.13.3
```

Relate it to dependencies:
```shell
juju switch microk8s:datahub-k8s
juju integrate datahub-k8s postgresql-k8s
juju integrate datahub-k8s kafka-k8s
juju integrate datahub-k8s lxd:admin/datahub-vm.os-client
```

You can use `juju refresh` as follows to avoid having to re-do relations between changes to the charm:
```shell
juju switch microk8s:datahub-k8s
cd /path/to/charm/dir
juju refresh --path=./datahub-k8s_ubuntu-22.04-amd64.charm datahub-k8s
```
