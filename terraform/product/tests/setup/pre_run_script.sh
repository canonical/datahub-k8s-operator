#!/bin/bash
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Pre-run setup for the product Terraform test. Sets the kernel sysctls OpenSearch requires so its
# units reach `active` on the LXD machine cloud. LXD containers inherit these from the runner host.
set -euo pipefail

echo "vm.max_map_count=262144
vm.swappiness=0
net.ipv4.tcp_retries2=5
fs.file-max=1048576" | sudo tee /etc/sysctl.d/99-charm-dev.conf

sudo sysctl --system
