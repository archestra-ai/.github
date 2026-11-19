#!/usr/bin/env bash
# Install the pinned gh-actions-lock release into $RUNNER_TEMP/gh-actions-lock
# and add it to the job's PATH. The release publishes no checksums or
# attestations, so the SHA-256 below was computed from the downloaded
# linux-amd64 binary. To update: change both values together.
set -euo pipefail

VERSION="v0.4.1"
SHA256="39deb2fe714dbf74b073d8245fc7f3d50cd0eee18d5c0900e9e849ae933ae17e" # linux-amd64

directory="${RUNNER_TEMP:?}/gh-actions-lock"
mkdir -p "$directory"
curl -fsSL --retry 3 -o "$directory/gh-actions-lock" \
  "https://github.com/github/gh-actions-lock/releases/download/${VERSION}/linux-amd64"
echo "${SHA256}  $directory/gh-actions-lock" | sha256sum -c -
chmod +x "$directory/gh-actions-lock"
echo "$directory" >> "${GITHUB_PATH:?}"
"$directory/gh-actions-lock" --version
