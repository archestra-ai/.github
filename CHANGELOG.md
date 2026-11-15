# Changelog

## [1.30.0](https://github.com/archestra-ai/.github/compare/v1.29.0...v1.30.0) (2026-11-15)


### Features

* **release-finalize:** reusable workflow that publishes a handed-off release ([#90](https://github.com/archestra-ai/.github/issues/90)) ([b166d05](https://github.com/archestra-ai/.github/commit/b166d05ce0b9c9ff8509a63eeea9515ca38b3e76))

## [1.29.0](https://github.com/archestra-ai/.github/compare/v1.28.0...v1.29.0) (2026-11-15)


### Features

* **actions:** release-finalize finishes a release handed off to release-publisher ([#88](https://github.com/archestra-ai/.github/issues/88)) ([888426f](https://github.com/archestra-ai/.github/commit/888426ff57a4c2c927a43f87e16bf45760e08a58))

## [1.28.0](https://github.com/archestra-ai/.github/compare/v1.27.0...v1.28.0) (2026-11-15)


### Features

* **release-publish:** attest each image's SBOMs and secret scan before the publish request ([#86](https://github.com/archestra-ai/.github/issues/86)) ([45d97ea](https://github.com/archestra-ai/.github/commit/45d97ea3fba33d86801ae628b20206ef262cb810))

## [1.27.0](https://github.com/archestra-ai/.github/compare/v1.26.0...v1.27.0) (2026-11-15)


### Features

* **actions:** attest-release-image signs SBOMs, a secret scan and a vulnerability scan for a release image ([#83](https://github.com/archestra-ai/.github/issues/83)) ([844e53f](https://github.com/archestra-ai/.github/commit/844e53fb105b532ce06735f2a51f049b2b4a7eff))


### CI/CD

* run every Python test with one command ([#84](https://github.com/archestra-ai/.github/issues/84)) ([686b269](https://github.com/archestra-ai/.github/commit/686b26971f49bf55d875bb72de0c13d939893834))

## [1.26.0](https://github.com/archestra-ai/.github/compare/v1.25.2...v1.26.0) (2026-11-14)


### Features

* **supply-chain-policy:** verify the signatures of the images Dockerfiles build on ([#82](https://github.com/archestra-ai/.github/issues/82)) ([e398326](https://github.com/archestra-ai/.github/commit/e3983263b29f9fec0741112e10822a5859b50287))


### Dependencies

* bump the github-actions-dependencies group across 3 directories with 5 updates ([#80](https://github.com/archestra-ai/.github/issues/80)) ([3997965](https://github.com/archestra-ai/.github/commit/3997965ffcf49cd3ffa8c74aed5ec419f03243f4))

## [1.25.2](https://github.com/archestra-ai/.github/compare/v1.25.1...v1.25.2) (2026-11-12)


### Bug Fixes

* **actions:** retry transient GitHub API errors in previous-release-published ([#78](https://github.com/archestra-ai/.github/issues/78)) ([01c9fdd](https://github.com/archestra-ai/.github/commit/01c9fdd818017fb4dbcf46dde8f6e1ebdc5c3784))

## [1.25.1](https://github.com/archestra-ai/.github/compare/v1.25.0...v1.25.1) (2026-11-10)


### CI/CD

* **release:** release this repository with Release Please ([#76](https://github.com/archestra-ai/.github/issues/76)) ([e122cc9](https://github.com/archestra-ai/.github/commit/e122cc92e36bf00aa7184064fb4ab784c2bbf357))
