#!/usr/bin/env bash
# Checks one platform of an image with Dockle (the CIS Docker Benchmark's
# image checks and Dockle's own) and writes the image benchmark predicate
# (benchmark.py). Used by this action, by the Image Benchmark workflow before
# a change merges, and by attest-release-image at release time, so all apply
# the same checks and waivers.
#
# The image is read from the Docker daemon: one already there (a candidate the
# workflow built and loaded) is used as is, otherwise it is pulled. Dockle gets
# it under a local tag, because given an index digest it reads the registry
# instead and rejects the platform manifest; a local tag it cannot find fails
# rather than falling back to another image.
#
# Exit status: 0 with the predicate written, 2 when a FATAL or WARN check no
# waiver covers failed (with on-failure fail); anything else is an error.
#
# Usage: benchmark.sh <image> <platform> <key> <waivers file or ""> <predicate.json> <work dir> <fail|record>
set -euo pipefail

# Bump the version and its hash together, from the release's checksums file,
# once the release is at least seven days old.
DOCKLE_VERSION=0.4.15
DOCKLE_SHA256=8bfc9183d1d3d67800edb45bea4bb63ddf1d28bec5f89944075a07007d79caad

image=$1 platform=$2 key=$3 waivers=$4 predicate=$5 work=$6 on_failure=$7
here=$(cd "$(dirname "$0")" && pwd)
case "$on_failure" in fail|record) ;; *) echo "::error::on-failure must be fail or record"; exit 1 ;; esac
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) ;;
  *) echo "::error::image-benchmark runs on Linux x86_64 runners only"; exit 1 ;;
esac

if [ ! -x "$work/bin/dockle" ]; then
  mkdir -p "$work/bin"
  curl --fail --silent --show-error --location --retry 3 --output "$work/dockle.tar.gz" \
    "https://github.com/goodwithtech/dockle/releases/download/v${DOCKLE_VERSION}/dockle_${DOCKLE_VERSION}_Linux-64bit.tar.gz"
  echo "$DOCKLE_SHA256  $work/dockle.tar.gz" | sha256sum --check --quiet
  tar -xzf "$work/dockle.tar.gz" -C "$work/bin" dockle
  rm "$work/dockle.tar.gz"
fi

docker image inspect "$image" >/dev/null 2>&1 || docker pull --quiet --platform "$platform" "$image" >/dev/null
scan="image-benchmark/scan:$$-${RANDOM}"
docker tag "$image" "$scan"
trap 'docker image rm "$scan" >/dev/null 2>&1 || true' EXIT

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
# Exit 0 whatever it finds: benchmark.py applies the waivers and the policy.
"$work/bin/dockle" --format json --output "$work/dockle.json" --exit-code 0 --timeout 15m "$scan" 2> "$work/dockle.log" \
  || { tail -n 20 "$work/dockle.log"; echo "::error::dockle could not check $image"; exit 1; }
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python3 -I "$here/benchmark.py" "$work/dockle.json" "$predicate" --image "$image" --key "$key" \
  --version "$DOCKLE_VERSION" --waivers "$waivers" --started "$started" --finished "$finished" --on-failure "$on_failure"
