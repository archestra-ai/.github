#!/usr/bin/env bash
# Install the pinned notation release for verify_base_images.py into
# $RUNNER_TEMP/notation and add it to the job's PATH. The archive is checked
# against the SHA-256 below (from the release's notation_<version>_checksums.txt).
# To update: change both values together.
set -euo pipefail

VERSION="1.3.2"
SHA256="e1a0f060308086bf8020b2d31defb7c5348f133ca0dba6a1a7820ef3cbb6dfe5" # notation_1.3.2_linux_amd64.tar.gz

directory="${RUNNER_TEMP:?}/notation"
archive="$directory/notation.tar.gz"
mkdir -p "$directory"
curl -fsSL --retry 3 -o "$archive" \
  "https://github.com/notaryproject/notation/releases/download/v${VERSION}/notation_${VERSION}_linux_amd64.tar.gz"
echo "${SHA256}  $archive" | sha256sum -c -
tar -xzf "$archive" -C "$directory" notation
echo "$directory" >> "${GITHUB_PATH:?}"
# Not `notation version | grep -m1`: grep exits at its first match, and
# notation writing the rest then dies of SIGPIPE, which pipefail turns into
# an intermittent exit 141.
version_output="$("$directory/notation" version)"
grep -m1 Version <<<"$version_output"
