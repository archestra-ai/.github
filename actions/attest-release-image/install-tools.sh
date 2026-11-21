#!/usr/bin/env bash
# Installs syft, trufflehog and oras from their GitHub releases, pinned by SHA-256.
# Bump a version and its hash together, from the release's checksums file,
# once the release is at least seven days old.
#
# Usage: install-tools.sh <bin directory>
set -euo pipefail

SYFT_VERSION=1.57.0
SYFT_SHA256=9c077a7d43029b3b13e98a8db720661e700c4d58db6352153a06395ae4a2a704
TRUFFLEHOG_VERSION=3.99.8
TRUFFLEHOG_SHA256=7c3b02f160f28929a467e29e20fba98746e0729f6e37c3c25761e4f8789abe08
ORAS_VERSION=1.3.6
ORAS_SHA256=66fb0ad7206f3f6fa77ed5b552049d364ccf4b50b1684554f731f76302b52ca0

bin=$1
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ;;
  *) echo "::error::attest-release-image runs on Linux x86_64 runners only"; exit 1 ;;
esac
mkdir -p "$bin"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fetch_tool() {
  local name=$1 url=$2 sha=$3
  curl --fail --silent --show-error --location --retry 3 --output "$work/$name.tar.gz" "$url"
  echo "$sha  $work/$name.tar.gz" | sha256sum --check --quiet
  tar -xzf "$work/$name.tar.gz" -C "$work" "$name"
  install -m 0755 "$work/$name" "$bin/$name"
}

fetch_tool syft \
  "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" \
  "$SYFT_SHA256"
fetch_tool trufflehog \
  "https://github.com/trufflesecurity/trufflehog/releases/download/v${TRUFFLEHOG_VERSION}/trufflehog_${TRUFFLEHOG_VERSION}_linux_amd64.tar.gz" \
  "$TRUFFLEHOG_SHA256"
fetch_tool oras \
  "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz" \
  "$ORAS_SHA256"

{
  echo "syft-version=$SYFT_VERSION"
  echo "trufflehog-version=$TRUFFLEHOG_VERSION"
} >> "${GITHUB_OUTPUT:-/dev/null}"
