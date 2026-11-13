#!/usr/bin/env bash
# Install the pinned cosign release for verify_base_images.py into
# $RUNNER_TEMP/cosign and add it to the job's PATH. The binary is checked
# against the SHA-256 below (from the release's cosign_checksums.txt).
# To update: change both values together.
set -euo pipefail

VERSION="v3.1.4"
SHA256="424e2c97d0b45fd7a6b608a87f090f35633f4c23ece5f092d48bba11aaf4bbfa" # cosign-linux-amd64

directory="${RUNNER_TEMP:?}/cosign"
mkdir -p "$directory"
curl -fsSL --retry 3 -o "$directory/cosign" \
  "https://github.com/sigstore/cosign/releases/download/${VERSION}/cosign-linux-amd64"
echo "${SHA256}  $directory/cosign" | sha256sum -c -
chmod +x "$directory/cosign"
echo "$directory" >> "${GITHUB_PATH:?}"
"$directory/cosign" version | grep -m1 GitVersion
