# Changelog

## [1.44.1](https://github.com/archestra-ai/.github/compare/v1.44.0...v1.44.1) (2026-11-22)


### Bug Fixes

* **attest-provenance:** let an image repository hold several digests ([#135](https://github.com/archestra-ai/.github/issues/135)) ([7b45d42](https://github.com/archestra-ai/.github/commit/7b45d428f3e6445b9b738de9670adecca6f905a1))
* **release-publish:** attest with the v1.44.0 actions, which skip unlogged DHI VEX ([#136](https://github.com/archestra-ai/.github/issues/136)) ([35384ad](https://github.com/archestra-ai/.github/commit/35384adb3a7b23bbe91af78fcb89ee2e6001f6c0))

## [1.44.0](https://github.com/archestra-ai/.github/compare/v1.43.0...v1.44.0) (2026-11-21)


### Features

* **attest-release-image:** carry over DHI VEX for Debian bases, skip attestations Rekor does not record ([#133](https://github.com/archestra-ai/.github/issues/133)) ([1564639](https://github.com/archestra-ai/.github/commit/15646392dd8bb166f13e8d8f6063c26727a429a5))

## [1.43.0](https://github.com/archestra-ai/.github/compare/v1.42.0...v1.43.0) (2026-11-21)


### Features

* **release-publish:** attest Docker Hardened Images' VEX for the caller's DHI bases ([#131](https://github.com/archestra-ai/.github/issues/131)) ([14ea621](https://github.com/archestra-ai/.github/commit/14ea62138ceecfdd6268ae82fed3a63acc34f4f7))

## [1.42.0](https://github.com/archestra-ai/.github/compare/v1.41.1...v1.42.0) (2026-11-21)


### Features

* **attest-release-image:** carry Docker Hardened Images' signed VEX over to images built from them ([#128](https://github.com/archestra-ai/.github/issues/128)) ([4868e12](https://github.com/archestra-ai/.github/commit/4868e12f40b95b5340655c19e94a9159ee048ac3))


### Bug Fixes

* **attest-release-image:** match the dhi.io registry host exactly ([#130](https://github.com/archestra-ai/.github/issues/130)) ([4d47d29](https://github.com/archestra-ai/.github/commit/4d47d2972219c8404dbcf8b51dc71ce07ca2b4b2))

## [1.41.1](https://github.com/archestra-ai/.github/compare/v1.41.0...v1.41.1) (2026-11-21)


### Bug Fixes

* **supply-chain-policy:** stop the cosign and notation installers failing on SIGPIPE ([#126](https://github.com/archestra-ai/.github/issues/126)) ([22f70ce](https://github.com/archestra-ai/.github/commit/22f70ce77350c723d1ba56a6149976ada171add6))

## [1.41.0](https://github.com/archestra-ai/.github/compare/v1.40.0...v1.41.0) (2026-11-21)


### Features

* **supply-chain-policy:** verify Docker Hardened Images with Docker's signing key ([#124](https://github.com/archestra-ai/.github/issues/124)) ([7de0fc8](https://github.com/archestra-ai/.github/commit/7de0fc845e0b5bcfdab9889145bae33d0b2120cc))

## [1.40.0](https://github.com/archestra-ai/.github/compare/v1.39.0...v1.40.0) (2026-11-21)


### Features

* **release-publish:** attest each image's benchmark with the caller's waivers ([#121](https://github.com/archestra-ai/.github/issues/121)) ([e58de29](https://github.com/archestra-ai/.github/commit/e58de2987b7a018c426b028fdc3563540b721506))

## [1.39.0](https://github.com/archestra-ai/.github/compare/v1.38.0...v1.39.0) (2026-11-21)


### Features

* **image-benchmark:** check images against the CIS Docker Benchmark before merge and at release ([#120](https://github.com/archestra-ai/.github/issues/120)) ([3ca126d](https://github.com/archestra-ai/.github/commit/3ca126d83a6113d5bf9e9e72e0960f3206709438))

## [1.38.0](https://github.com/archestra-ai/.github/compare/v1.37.2...v1.38.0) (2026-11-21)


### Features

* **release-publish:** attest and stage image variants under their repository ([#118](https://github.com/archestra-ai/.github/issues/118)) ([28b29ee](https://github.com/archestra-ai/.github/commit/28b29ee52b2c39c9419b91f18af785c8814fb963))

## [1.37.2](https://github.com/archestra-ai/.github/compare/v1.37.1...v1.37.2) (2026-11-21)


### Bug Fixes

* **release-publish:** run the malware scan from v1.37.1 ([#116](https://github.com/archestra-ai/.github/issues/116)) ([e9cbde6](https://github.com/archestra-ai/.github/commit/e9cbde6cc8c56fdf9e005842ff90a5eb523066af))

## [1.37.1](https://github.com/archestra-ai/.github/compare/v1.37.0...v1.37.1) (2026-11-21)


### Bug Fixes

* **attest-release-image:** scan files under nested dev directories ([#114](https://github.com/archestra-ai/.github/issues/114)) ([b869b34](https://github.com/archestra-ai/.github/commit/b869b3460a581a8796b6f84e2618ccde198b142e))

## [1.37.0](https://github.com/archestra-ai/.github/compare/v1.36.0...v1.37.0) (2026-11-21)


### Features

* **release-publish:** attest a ClamAV malware scan and a SLSA verification summary for each image ([#112](https://github.com/archestra-ai/.github/issues/112)) ([6757306](https://github.com/archestra-ai/.github/commit/6757306a10104c6c06b6aed52a9fa723630c598f))

## [1.36.0](https://github.com/archestra-ai/.github/compare/v1.35.0...v1.36.0) (2026-11-20)


### Features

* **attest-release-image:** attest a ClamAV malware scan and a SLSA verification summary ([#111](https://github.com/archestra-ai/.github/issues/111)) ([7edbd0b](https://github.com/archestra-ai/.github/commit/7edbd0bd3acc67a3baae780e556f8b08d20bc65a))


### Dependencies

* bump the github-actions-dependencies group across 3 directories with 4 updates ([#110](https://github.com/archestra-ai/.github/issues/110)) ([3d5bfec](https://github.com/archestra-ai/.github/commit/3d5bfec69d246c8ce85d0c3bec85946a59da2a9c))


### Miscellaneous Chores

* remove the Archestra runtime review workflow ([#106](https://github.com/archestra-ai/.github/issues/106)) ([d0f5dfe](https://github.com/archestra-ai/.github/commit/d0f5dfedc0b53845423ba88d47d2441c553e9210))

## [1.35.0](https://github.com/archestra-ai/.github/compare/v1.34.0...v1.35.0) (2026-11-20)


### Features

* **release-publish:** read the VEX document from vex-ref ([#107](https://github.com/archestra-ai/.github/issues/107)) ([1cf0aa0](https://github.com/archestra-ai/.github/commit/1cf0aa0bda758648cafbe1ec8c4353db560b18ab))

## [1.34.0](https://github.com/archestra-ai/.github/compare/v1.33.0...v1.34.0) (2026-11-19)


### Features

* **release-publish:** attest each image's OpenVEX statements from the caller's vex-document ([#97](https://github.com/archestra-ai/.github/issues/97)) ([950521e](https://github.com/archestra-ai/.github/commit/950521efb8088b6b38fe60a43e508371d2633046))

## [1.33.0](https://github.com/archestra-ai/.github/compare/v1.32.1...v1.33.0) (2026-11-19)


### Features

* **attest-release-image:** attest OpenVEX statements, with one shared vex.py and a validate-vex action ([#96](https://github.com/archestra-ai/.github/issues/96)) ([2f8f2fe](https://github.com/archestra-ai/.github/commit/2f8f2fe07c438cec26ac719ad82525ac0f6509e2))

## [1.32.1](https://github.com/archestra-ai/.github/compare/v1.32.0...v1.32.1) (2026-11-19)


### Bug Fixes

* **release-please:** lock reusable workflows by tag so callers pass the lockfile policy ([#102](https://github.com/archestra-ai/.github/issues/102)) ([63733b2](https://github.com/archestra-ai/.github/commit/63733b2542c5c6ebe8e96d85b58cc9cef4c9e2fe))

## [1.32.0](https://github.com/archestra-ai/.github/compare/v1.31.1...v1.32.0) (2026-11-19)


### Features

* **supply-chain-policy:** require a GitHub Actions workflow lockfile ([#99](https://github.com/archestra-ai/.github/issues/99)) ([ca83adf](https://github.com/archestra-ai/.github/commit/ca83adf5b2dc2c7816420b2017c45c8a723b8715))


### Bug Fixes

* **release-please:** pin actions in reusable workflows to commit SHAs ([#100](https://github.com/archestra-ai/.github/issues/100)) ([336a32f](https://github.com/archestra-ai/.github/commit/336a32f302348afb9fd1a38320f8b505497c66dd))


### CI/CD

* lock GitHub Actions dependencies in a workflow lockfile ([#98](https://github.com/archestra-ai/.github/issues/98)) ([b77f782](https://github.com/archestra-ai/.github/commit/b77f78219e99a512921a8f9aaccf2b47005e0181))

## [1.31.1](https://github.com/archestra-ai/.github/compare/v1.31.0...v1.31.1) (2026-11-16)


### Bug Fixes

* **release-finalize:** verify with release-publish-verify v1.31.0, which accepts the shared provenance signer ([#94](https://github.com/archestra-ai/.github/issues/94)) ([3387726](https://github.com/archestra-ai/.github/commit/33877265fc50b6f288ac1bdab13f9e10050e3cf3))

## [1.31.0](https://github.com/archestra-ai/.github/compare/v1.30.0...v1.31.0) (2026-11-16)


### Features

* **attest-provenance:** reusable workflow that signs build provenance in a job of its own ([#92](https://github.com/archestra-ai/.github/issues/92)) ([f203a96](https://github.com/archestra-ai/.github/commit/f203a96bdc9b9132feba59cc8322dde1c93f7286))

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
