# Browser simulator integration

The browser simulator is maintained in the dedicated
[`Schnuartz/specter-diy-web-simulator`](https://github.com/Schnuartz/specter-diy-web-simulator)
repository. This repository keeps only the small integration layer needed to
resolve the exact Specter source commit and record firmware provenance.

The `Build` workflow pins the simulator reusable workflows and tooling to a
full commit SHA. It passes both immutable inputs to the simulator repository:

- the exact Specter-DIY source repository and commit to build;
- the exact simulator repository and commit used for the browser shell,
  WebAssembly build, verification, tests, and trusted preview publisher.

The caller remains responsible for the GitHub token, pull-request context,
artifacts, Pages branch, and deployment. The simulator workflows never use a
cross-repository dispatch or a personal access token, which keeps fork PRs
usable with the ordinary read-only build token.

The simulator repository documents local Emscripten builds, browser smoke
tests, provenance manifests, and preview publication. Preview links are
published by the trusted workflow and remain under:
`https://<owner>.github.io/specter-diy/pr/<number>/`.

All browser and firmware builds are experimental development builds. Never
enter a real seed phrase or use real funds.
