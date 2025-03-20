# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

You can create an environment for development with `tox`:

```shell
tox devenv -e integration
source venv/bin/activate
```

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox run -e fmt           # update your code according to linting rules
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
juju add-model datahub-vm
juju switch microk8s
juju add-model datahub-k8s
# Refer to [2] below for a note.
# Refer to [3] below for a note.
```

[1]: Minor and/or patch versions of the tech stack can introduce regressions, if you are having problems use the following commands to switch to tested channels:
```shell
sudo snap switch juju --channel 3.5/stable
sudo snap refresh juju
sudo snap switch microk8s --channel 1.29/stable
sudo snap refresh microk8s --classic
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

## Deploy the dependencies

DataHub has PostgreSQL, Kafka, and OpenSearch as dependencies. Opensearch only has a machine charm, while the others have both a machine and a Kubernetes charm. In production, all of the dependencies are intended to be deployed on machines but for simplification of development purposes you can have only Opensearch on a machine and deploy the rest on `microk8s`. So, the instructions here will cover that scenario.

Deploying Opensearch:
```shell
juju switch lxd:datahub-vm
# Less than 3 units might work for a while but is prone to blocking
juju deploy opensearch --channel 2/edge -n 2
juju deploy self-signed-certificates
juju deploy postgresql --channel 14/stable
juju deploy kafka --channel 3/edge
juju deploy zookeeper
juju integrate opensearch self-signed-certificates
juju integrate kafka zookeeper
juju offer opensearch:opensearch-client os-client
juju offer postgresql:database pg-client
juju offer kafka:kafka-client kafka-client
```

Deploying other dependencies:
```shell
juju switch microk8s:datahub-k8s
juju consume lxd:admin/datahub-vm.os-client os-client
juju consume lxd:admin/datahub-vm.pg-client pg-client
juju consume lxd:admin/datahub-vm.kafka-client kafka-client
```

**Note:** In theory, DataHub can work with both VM and K8s versions of Kafka and PostgreSQL charms.

## Build the charm

Get the repository on the host machine using:
```shell
git clone git@github.com:canonical/datahub-k8s-operator.git
```

For easier development, mount the repository to the `multipass` instance:
```shell
multipass mount /path/to/repository charm-dev:/path/to/repository
```

Build the charm inside the `multipass` instance using:
```shell
cd /path/to/repository
charmcraft pack
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

## Deploy the charm

Deploy the charm after the build step with the following command:
```shell
juju switch microk8s:datahub-k8s
cd /path/to/charm/dir

# Create a secret for encryption keys
echo -e "gms-key: $GMS_SECRET\nfrontend-key: $FE_SECRET" > /path/to/secret.yaml
juju add-secret <secret-name> --file=/path/to/secret.yaml  # copy the ID from the output

# Deploy
juju deploy ./datahub-k8s_ubuntu-22.04-amd64.charm \
--resource datahub-actions=acryldata/datahub-actions:v0.0.15 \
--resource datahub-frontend=acryldata/datahub-frontend-react:v0.13.3 \
--resource datahub-gms=acryldata/datahub-gms:v0.13.3 \
--resource datahub-opensearch-setup=acryldata/datahub-elasticsearch-setup:v0.13.3 \
--resource datahub-kafka-setup=acryldata/datahub-kafka-setup:v0.13.3 \
--resource datahub-postgresql-setup=acryldata/datahub-postgres-setup:v0.13.3 \
--resource datahub-upgrade=acryldata/datahub-upgrade:v0.13.3 \
--config encryption-keys-secret-id=<secret-id>
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

After the final command, it will take some time for the `datahub-frontend` container to settle. Once it does, you can login on `localhost:9002` with `datahub` for both username and password. Refer to [below](#accessing-datahub-from-the-host-machine) on how to connect to a `datahub` deployment inside a `multipass` VM.

**Note:** If the Opensearch offer is blocked from the provider end, DataHub will load but some functionalities such as `Ingestion` will not load. This is best identified by the requests to `/graphql` returning a `500`.

## Iterating on the charm

You can use `juju refresh` as follows to avoid having to re-do relations between changes to the charm:
```shell
juju switch microk8s:datahub-k8s
cd /path/to/charm/dir
juju refresh --path=./datahub-k8s_ubuntu-22.04-amd64.charm datahub-k8s
```

Sometimes, it is required to re-deploy the application. For faster iteration do the following:
```shell
juju switch microk8s:datahub-k8s
juju remove-application datahub-k8s --force
watch -n2 "kubectl -n datahub-k8s get pod"  # wait until 'datahub-k8s' is 'Terminating'
kubectl -n datahub-k8s delete pod --force --grade-period=0 datahub-k8s-0  # pod name is prone to change
watch -n2 "kubectl -n datahub-k8s get pod"  # make sure 'datahub-k8s' is gone and wait a few seconds
# Re-deploy and re-integrate
juju deploy ...
```

## Accessing DataHub from the host machine

The recommended setup deploys DataHub inside of a VM. Which requires extra work to make it accessible from the host machine where you would have your browser. We can solve this with a few steps.

First, get the IP address of your `multipass` instance, run this on the host:
```shell
❯ multipass info charm-dev
Name:           charm-dev
State:          Running
Snapshots:      0
IPv4:           10.150.222.16
                10.214.223.1
                10.1.157.64
Release:        Ubuntu 22.04.4 LTS
Image hash:     578ec657151d (Ubuntu 22.04 LTS)
CPU(s):         4
Load:           0.79 0.54 0.49
Disk usage:     41.0GiB out of 48.4GiB
Memory usage:   8.9GiB out of 15.6GiB
Mounts:         /home/user/Workspaces => ~/Workspaces
                    UID map: 1000:default
                    GID map: 1000:default
```

We are interested in the first IP address in the list, in this case `10.150.222.16`.

Second, get the IP address of your `datahub` application, run this in the `multipass` instance:
```shell
ubuntu@charm-dev:~$ juju status
Model        Controller  Cloud/Region        Version  SLA          Timestamp
datahub-k8s  microk8s    microk8s/localhost  3.5.3    unsupported  12:41:52+03:00

SAAS       Status  Store  URL
os-client  active  lxd    admin/datahub-vm.os-client
pg-client  active  lxd    admin/datahub-vm.pg-client

App            Version  Status  Scale  Charm          Channel   Rev  Address         Exposed  Message
datahub-k8s             active      1  datahub-k8s               28  10.152.183.245  no
kafka-k8s               active      1  kafka-k8s      3/stable   56  10.152.183.120  no
zookeeper-k8s           active      1  zookeeper-k8s  3/stable   51  10.152.183.26   no

Unit              Workload  Agent  Address      Ports  Message
datahub-k8s/0*    active    idle   10.1.157.75
kafka-k8s/0*      active    idle   10.1.157.87
zookeeper-k8s/0*  active    idle   10.1.157.79
```

We are interested in the address of `datahub-k8s`, in this case `10.152.183.245`.

Third, on the host machine route IPs:
```shell
sudo ip route add 10.152.183.0/24 via 10.150.222.16  # replace the last IP address with the address from the first step
```

This change will persist until reboot. Now, you can go into your browser with the address of the `datahub-k8s` application such as `10.152.183.245:9002` and access DataHub.
