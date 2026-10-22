#!/bin/bash
# Installs a pinned Azure CLI version on a GitHub-hosted Ubuntu runner.
# This is the single place to change the Azure CLI version used by the workflows.
# The version must be published in Microsoft's apt repo for the runner's distribution.

set -o errexit
set -o pipefail
set -o nounset

AZ_VER="2.91.0"
AZ_DIST=$(lsb_release -cs)

apt-cache policy azure-cli
sudo apt-get install -y azure-cli="${AZ_VER}-1~${AZ_DIST}" --allow-downgrades
az version
