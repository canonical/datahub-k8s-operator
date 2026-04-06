# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

A lot of the commands you would need are covered with the [Makefile](./Makefile), learn more by running `make help`.

**Note:** It is recommended to build on the host and deploy in a [Multipass](https://canonical.com/multipass/install) instance.
Use `multipass mount` to mount the project directory with the build artifacts into your Multipass instance.

## Environment for coding

You can install the dependencies for coding with:

```shell
# uv
sudo snap install astral-uv --channel latest/stable --classic
uv version
#> uv 0.9.5 (d5f39331a 2025-10-21)

# Tox
# Note: If you do this from the VSCode snap's integrated terminal
# you will have a weird PATH. So, do it from an external terminal.
uv tool install tox --with tox-uv
tox --version
#> 4.32.0
```

You can create an environment for coding with:

```shell
make venv
source venv/bin/activate
```

## Environment for building

You can install the dependencies for building with:

```shell
# LXD
sudo snap install lxd --channel 5.21/stable
lxd version
#> 5.21.4 LTS

sudo adduser $USER lxd
newgrp lxd
lxd init --auto

# Charmcraft
sudo snap install charmcraft --channel latest/stable --classic
charmcraft version
#> charmcraft 4.0.1

# Rockcraft
sudo snap install rockcraft --channel latest/stable --classic
rockcraft version
#> rockcraft 1.16.0

# yq
sudo snap install yq
yq --version
#> yq (https://github.com/mikefarah/yq/) version v4.49.2

# Required by `import_rock.sh`
sudo snap alias rockcraft.skopeo skopeo
```

### Verify build environment

You can verify that you have all the necessary dependencies installed with:

```shell
make check-build-deps
```

## Building artifacts

You can build the charm with:

```shell
make build-charm
```

You can build all rocks with:

```shell
make build-rock
```

Individual rocks can also be built separately:

```shell
make build-rock-actions
make build-rock-frontend
make build-rock-gms
```

**Common issues:** Some frequent issues with the build step and how to solve them are listed below.
1. *Missing repository*: Sometimes `multipass` forgets mounts. The solution is to unmount and mount again, from the host machine:
```shell
multipass unmount charm-dev
multipass mount /path/to/repository charm-dev:/path/to/repository
```

2. *Permission error in pack*: `tox` commands create directories inside the repository that can cause permission issues. The solution is to remove those directories before running `charmcraft pack`.
```shell
cd /path/to/repository
rm -rf .tox venv
```

3. *Nothing to be done for rock*: Rock building targets watch for changes to the `rockcraft.yaml` file for deciding if they need to rebuild. If you need to change something else, e.g. scripts that go into the rocks, `touch rockcraft.yaml` to force a build.

## Using commit hashes for upstream versions

We use commit hashes instead of git tags in our `rockcraft.yaml` files. This is a security measure to ensure that upstream git tags cannot be updated to point to a malicious commit without our knowledge, which could lead to supply chain attacks. By pinning to the exact commit hash of a release tag, we ensure reproducibility and security.

### How to update to a newer tag

When updating the rocks to a newer version of DataHub (e.g. going from `v1.4.0.5` to `v1.4.1`):

1. Find the commit hash for the new tag in the `acryldata/datahub` repository. You can do this by running:
   ```shell
   git ls-remote https://github.com/acryldata/datahub.git refs/tags/v1.4.1
   ```
2. In each `rockcraft.yaml`, update the `source-commit` field to the new hash.
3. Update the comment next to `source-commit` to reflect the new tag (e.g. `# v1.4.1`).
4. Update other references to the version (such as `version` and `summary`) as necessary.

## Code quality

You can run linters, static analysis, and unit tests with:

```shell
make fmt     # Runs formatters
make lint    # Runs linters
make test    # Runs static analysis and unit tests
make checks  # Runs all of the above

make test-integration  # Runs integration tests*
```

*: It is recommended to let CI runners run integration tests on GitHub Actions.

## Deploying locally

### Multipass environment setup

The recommended deployment environment is set up on `multipass`.

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
juju add-model datahub-vm
juju switch microk8s
juju add-model datahub-k8s
# Refer to [2] below for a note.
# Refer to [3] below for a note.
```

[1]: Minor and/or patch versions of the tech stack can introduce regressions, if you are having problems use the following commands to switch to tested channels:
```shell
sudo snap switch juju --channel 3/stable
sudo snap refresh juju
sudo snap switch microk8s --channel 1.34-strict/stable
sudo snap refresh microk8s
```

Afterwards, you will have to recreate the controllers and models from scratch:
```shell
juju bootstrap lxd lxd
juju add-model datahub-vm
juju bootstrap microk8s microk8s
juju add-model datahub-k8s
```

[2]: DataHub has Opensearch as a dependency. In order to deploy the Opensearch charm, you need to edit certain kernel parameters inside your `multipass` instance as instructed [here](https://charmhub.io/opensearch/docs/t-set-up#heading--kernel-parameters).

It is recommended to run the following command inside your `multipass` instance to make the changes permanent:
```shell
echo -e "vm.max_map_count=262144\nvm.swappiness=0\nnet.ipv4.tcp_retries2=5\nfs.file-max=1048576" | sudo tee -a /etc/sysctl.d/99-custom.conf
sudo sysctl --system
```

[3]: For a better development experience, you can turn on debug logging in a model with the following:
```shell
juju switch controller:model
juju model-config logging-config="<root>=INFO;unit=DEBUG"
```

### Deployment environment dependencies

Install the following inside your `multipass` instance:

```shell
# Juju
sudo snap install juju --channel 3/stable
juju version
#> 3.6.12-genericlinux-amd64

# MicroK8s
sudo snap install microk8s --channel 1.34-strict/stable
microk8s version
#> MicroK8s v1.34.1 revision 8447

sudo microk8s enable hostpath-storage
sudo microk8s enable registry

sudo usermod -aG snap_microk8s $USER

# Docker
sudo snap install docker --channel latest/stable
docker version
#> ... 28.4.0 ...

sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker

sudo snap disable docker
sudo snap enable docker

# Both microk8s and docker require new groups
# and `newgrp` does not cover for both at the same time.
# A system reboot is recommended at this point.

juju bootstrap microk8s
```

You can verify that all deployment dependencies are installed with:

```shell
make check-deploy-deps
```

### Deploy the dependencies

DataHub has PostgreSQL, Kafka, and OpenSearch as dependencies. Opensearch only has a machine charm, while the others have both a machine and a Kubernetes charm. However, the main deployment scenario is one where dependencies are deployed as machine charms. So, the documentation covers it.

Deploying dependencies and creating offers:
```shell
juju switch lxd:datahub-vm
# Opensearch charms needs a minimum of two units to cover 
# the indexes created by DataHub
juju deploy opensearch --channel 2/edge -n 2
juju deploy self-signed-certificates
juju integrate opensearch self-signed-certificates
juju offer opensearch:opensearch-client os-client
```

Consuming offers and deploying other dependencies:
```shell
juju switch microk8s:datahub-k8s
juju consume lxd:admin/datahub-vm.os-client os-client
juju deploy postgresql-k8s --channel 14/edge
juju deploy kafka-k8s --channel 3/edge
juju deploy zookeeper-k8s --channel 3/edge
juju integrate kafka-k8s zookeeper-k8s
```

**Note:** DataHub should work with both VM and K8s versions of Kafka and PostgreSQL charms.

### Deploy the charm

First, build and import the rocks into MicroK8s (run on the host where you built):

```shell
make import-rock
```

Then, create a secret for encryption keys and deploy:

```shell
juju switch microk8s:datahub-k8s

# Create a Juju secret with random encryption keys
make create-secret  # copy the secret ID from the output

# Deploy with local resources (pass the secret ID from the previous step)
make deploy-local SECRET_ID=<secret-id>

# Grant secret
juju grant-secret datahub-encryption-keys datahub-k8s
```

You can also customise the secret name:
```shell
make create-secret SECRET_NAME=my-custom-name
```

**Note:** The configuration variables need to be set at deployment time and they should not be changed afterwards. DataHub expects these secrets to be secure, so ensure you have a secret with sufficient entropy. Future updates will make QoL changes here to make this easier to manage.

Relate it to dependencies:
```shell
juju switch microk8s:datahub-k8s
juju integrate datahub-k8s pg-client
juju integrate datahub-k8s kafka-k8s
juju integrate datahub-k8s os-client
```

**Note:** It is highly recommended to wait between commands to let `juju status` show an `active-idle` status for the charm.

After the final command, it will take some time for the `datahub-frontend` container to settle. Once it does, you can login on `localhost:9002` with `datahub` for the username and the password fetched as described in the [README](./README.md#deploying-datahub). Refer to [below](#accessing-datahub-from-the-host-machine) on how to connect to a `datahub` deployment inside a `multipass` VM.

**Note:** If the Opensearch offer is blocked from the provider end, DataHub will load but some functionalities such as `Ingestion` will not load. This is best identified by the requests to `/graphql` returning a `500`.

**Note:** The GMS container uses `runner.sh` to execute initialization scripts (PostgreSQL setup, OpenSearch index creation, schema migration). The runner duplexes all output to log files under `/tmp` in the `datahub-gms` container. If an initialization step fails, you can list and read the logs with:
```shell
kubectl -n <namespace> exec -c datahub-gms datahub-k8s-0 -- ls /tmp
kubectl -n <namespace> exec -c datahub-gms datahub-k8s-0 -- cat /tmp/<log-file>
```
Environment variables used during each run are also saved alongside the logs as `.env` files.
