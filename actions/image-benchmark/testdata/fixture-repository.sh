#!/usr/bin/env bash
# Runs the Image Benchmark workflow's steps (plan.py, its build, benchmark.sh)
# against a fixture repository: an image that passes, one that runs as root
# and fails until a waiver covers it, and an undeclared Dockerfile that fails
# the plan. Needs Docker, git and jq on a Linux x86_64 host; $TOOLS is
# actions/image-benchmark.
set -euo pipefail

: "${TOOLS:?set TOOLS to the actions/image-benchmark directory}"
BASE_IMAGE=gcr.io/distroless/static@sha256:aafe993665bf6200db38a02c11a9351e782a84b6a45e7f33b6695638c88e3931
root=$(mktemp -d)
repo=$root/repo
trap 'rm -rf "$root"; docker image rm image-benchmark/fixture-nonroot image-benchmark/fixture-root >/dev/null 2>&1 || true' EXIT
mkdir -p "$repo"
cd "$repo"
git init --quiet --initial-branch=main
git config user.email fixture@example.com
git config user.name Fixture
echo fixture > README.md
git add --all && git commit --quiet --message base
base=$(git rev-parse HEAD)

mkdir -p nonroot root .github
printf 'FROM %s\nUSER 65532\n' "$BASE_IMAGE" > nonroot/Dockerfile
printf 'FROM %s\nUSER root\n' "$BASE_IMAGE" > root/Dockerfile
cat > .github/images.json <<'JSON'
{"images": [
  {"key": "fixture.example/nonroot", "dockerfile": "nonroot/Dockerfile", "context": "nonroot"},
  {"key": "fixture.example/root", "dockerfile": "root/Dockerfile", "context": "root"}
]}
JSON
git add --all && git commit --quiet --message images

python3 -I "$TOOLS/plan.py" plan . --base-ref "$base" --output "$root/plan"
matrix=$(sed -n 's/^matrix=//p' "$root/plan")
grep -qx 'build=true' "$root/plan"
test "$(jq '.include | length' <<<"$matrix")" = 2

# Builds and checks the entry for $1; prints benchmark.sh's exit status.
check() {
  local key=$1 waivers=$2 tag work status
  tag=image-benchmark/fixture-${key##*/}
  work=$(mktemp -d "$root/work.XXXXXX")
  IMAGE_ENTRY=$(jq -c --arg key "$key" '.include[] | select(.key == $key)' <<<"$matrix") \
    python3 -I "$TOOLS/plan.py" build . --tag "$tag" >&2
  # Indented, so an expected failure is not an annotation on the run.
  bash "$TOOLS/benchmark.sh" "$tag" linux/amd64 "$key" "$waivers" "$work/predicate.json" "$work" fail 2>&1 | sed 's/^/  /' >&2
  status=${PIPESTATUS[0]}
  echo "$status"
}

test "$(check fixture.example/nonroot "")" = 0
test "$(check fixture.example/root "")" = 2
echo "Unwaived root user failed, as expected."

cat > .github/image-benchmark-waivers.json <<'JSON'
{"waivers": [{"images": ["fixture.example/root"], "check": "CIS-DI-0001", "reason": "The fixture runs as root on purpose."}]}
JSON
test "$(check fixture.example/root .github/image-benchmark-waivers.json)" = 0
echo "Waived root user passed."

mkdir -p extra && printf 'FROM %s\n' "$BASE_IMAGE" > extra/Dockerfile
git add --all && git commit --quiet --message undeclared
if python3 -I "$TOOLS/plan.py" plan . --base-ref "$base" | sed 's/^/  /'; [ "${PIPESTATUS[0]}" = 0 ]; then
  echo "::error::An undeclared Dockerfile passed the plan."
  exit 1
fi
echo "Undeclared Dockerfile failed the plan, as expected."
