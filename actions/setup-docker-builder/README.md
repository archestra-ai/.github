# Shared Docker builder and cache

Use this action once before building images in each job. Consumers pin a reviewed
commit of `archestra-ai/.github/actions/setup-docker-builder`.

```yaml
- name: Set up Docker cache
  id: docker
  uses: archestra-ai/.github/actions/setup-docker-builder@<reviewed-commit>
  with:
    cache-scope: api-amd64
    cache-mode: max
    # Optional for native images that will be loaded and smoke-tested locally.
    # Run before creating any containers or pulling images needed later.
    driver: docker
- uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0
  with:
    context: .
    load: true
    tags: api:ci
    cache-from: ${{ steps.docker.outputs.cache-from }}
    cache-to: ${{ steps.docker.outputs.cache-to }}
```

## Cache routing and writers

Cache specifications deliberately omit a forced protocol version, URL, and
token. GitHub's runner selects its v2 service. Ubicloud's runner selects its
local cache proxy, which preserves branch isolation. Explicit `version=2`
bypasses that proxy on the runner examined during migration; its layers landed
in GitHub's 10 GB cache instead of Ubicloud's cache. Neither disabling branch
protection nor copying runtime credentials is needed.

`cache-write: auto` writes only on a default-branch push or manual run on that
branch. PRs and merge queues read that warm cache without uploading multi-GB
intermediates again. `true` explicitly selects a writer (for example a release
publisher); `false` selects a reader. A default-branch publisher must warm each
scope before this policy benefits new PRs. This setting is a performance policy;
the runner's own cache permissions remain the access-control boundary.

Give each unrelated image and native architecture a distinct stable scope.
CI and publishing of the same workload should use the same scope. Avoid one
writer per consumer: one designated job populates each scope. Cache entries
remain repository-isolated by the cache service, including private mirrors.

For jobs building several images, set up once without `cache-scope`, keep a
distinct existing `cache-from` per image, and guard each writer's `cache-to`
with `steps.docker.outputs.cache-write == 'true'`. Existing registry/GAR caches
can keep their current refs and credentials; this action does not require moving
those to GHA storage.

## Optional zstd registry cache

`cache-backend: ghcr` uses `ghcr.io/<owner>/<repo>-buildkit-cache:<scope>`
with zstd level 1, forced compression, OCI manifests, and the same designated
writer policy. Authentication uses the current job's `GITHUB_TOKEN`, not a PAT
or a cross-repository credential. Each new GHCR package is private by default;
this action never changes its visibility or access settings. Publishing with
`GITHUB_TOKEN` links the package to the source repository. If organization policy
disables permission inheritance, the package needs explicit repository access;
a permission error must be resolved rather than hidden. Fork PRs fall back to the
repository-isolated GHA backend without logging into the base repository cache.

The caller grants `packages: write` to its designated publisher and
`packages: read` to consumers, alongside its normal `contents: read` permission.
Use a distinct scope per image and native architecture. Keep scopes stable so
old package versions do not grow with each PR; package retention still needs
an explicit policy before a broad rollout.

```yaml
# Inside a job with packages: read (or write for its publisher):
- id: docker
  uses: archestra-ai/.github/actions/setup-docker-builder@<reviewed-commit>
  with:
    cache-backend: ghcr
    cache-scope: api-amd64
    cache-mode: max
    driver: docker
- uses: docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc # v7.4.0
  with:
    context: .
    tags: api:ci
    # Native Docker driver stores the image directly; don't also request load.
    outputs: type=image,compression=zstd,compression-level=1,oci-mediatypes=true
    cache-from: ${{ steps.docker.outputs.cache-from }}
    cache-to: ${{ steps.docker.outputs.cache-to }}
```

This profile is experimental until representative builds pass the runtime gate.
GHA's BuildKit exporter selects default gzip; adding compression=zstd to a GHA
cache spec does not select zstd. A registry cache supports that codec. GHCR
container image storage and bandwidth are currently free, including private
images; this is GitHub's current policy, not a permanent price guarantee.
Existing GAR caches can remain in place with their existing authentication.

Sources: [registry cache options](https://docs.docker.com/build/cache/backends/registry/),
[GHCR billing](https://docs.github.com/en/billing/concepts/product-billing/github-packages),
[GHCR permissions](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[BuildKit GHA compression](https://github.com/moby/buildkit/blob/8e20b4110a5482b200bebaf5e3d667a8f4446ac1/cache/remotecache/gha/gha.go#L176).

## Local image store

The default `docker-container` driver preserves the usual Buildx behavior.
For native smoke-test builds, `driver: docker` enables the containerd image
store in the runner's existing Docker Engine (28+). This supports external
caches while building directly into the image store consumed by `docker run`,
avoiding a separate Docker-tar export/import. It does not persist the runner.

This opt-in profile is for fresh ephemeral Linux runners. It refuses to switch
stores when any running or stopped containers exist. Preloaded images in the old
store become hidden, not deleted; run setup before pulling images you need.
Registry mirror/DNS and other daemon settings are preserved. Tests verify the
configuration is restored if restart fails. Do not use this profile on persistent
developer machines or after starting job/service containers.

## Runtime acceptance gate

Do not roll out a runner/cache change if equivalent CI exceeds 110% of its
Blacksmith baseline. Measure unchanged required checks on comparable commits,
with cold and warm states identified separately. Record end-to-end critical-path
latency (including queueing) and per-job/build-step times; compare median and p95
over repeated representative runs. A failed, skipped, or canceled check cannot
count as a speedup. Tiny contract tests here verify correctness, not performance
parity for application images.

External caches still transfer and unpack layers. This action does not promise
Blacksmith-equivalent persistent-disk performance. If representative warm runs
miss the 10% gate, keep the migration unmerged and evaluate a persistent BuildKit
backend with retained SSD state and cache mounts. A shared YAML action alone
cannot create that backend.

Sources: [Docker GHA cache](https://docs.docker.com/build/cache/backends/gha/),
[Ubicloud cache](https://www.ubicloud.com/docs/github-actions-integration/ubicloud-cache),
[Ubicloud runner cache handling](https://github.com/ubicloud/runner/blob/0ceae7abd4cb3cf1208c9ba68ad01b32a1d4bf90/src/Runner.Worker/Handlers/NodeScriptActionHandler.cs#L54),
[Docker containerd store](https://docs.docker.com/engine/storage/containerd/).
