# ESP32-P4 Web Flasher

This static page is deployed at <https://schnuartz-ai.github.io/specter-diy/>.
It is for the Waveshare ESP32-P4-WIFI6-Touch-LCD-4.3-C only. The browser reads
the chip revision and selects a pre-rev3 or v3.x firmware profile. It blocks
unsupported revisions and requires the user to confirm the board model, storage
reset, and experimental-build warning.

The release mode checks each downloaded image against the SHA-256 manifest in
that profile's `release.json`. Those hashes catch transfer or packaging errors;
they are not signed firmware identity. The source release and its checksums are
served by the same Pages site.

Each profile includes `storage-reset.bin` at `0x410000`. This invalidates the
internal filesystem so firmware can create it again. It is not a secure physical
flash erase. Use `ports/esp32p4/tools/build.sh erase` for a full-chip erase before
first installation or after a flash-layout change.

## Rebuild the published site

The publisher fetches the pre-rev3 ZIP from the selected Sandman
`esp32p4-*` release and preserves the current v3.x community bundle unless a
replacement is passed explicitly:

```sh
ports/esp32p4/tools/publish-flasher.sh --dry-run
ports/esp32p4/tools/publish-flasher.sh --pre-rev3-tag esp32p4-v0.1.0-alpha.7
ports/esp32p4/tools/publish-flasher.sh \
  --pre-rev3-tag esp32p4-v0.1.0-alpha.7 \
  --rev3-zip /path/to/rev3-community.zip \
  --rev3-tag "ESP32-P4 v3.x community build" \
  --rev3-url https://github.com/schnuartz-ai/specter-diy/releases
```

The script publishes to `schnuartz-ai/specter-diy` by default. Before pushing,
it requires `gh api user --jq .login` to report `schnuartz-ai`. Set
`PAGES_REPO=owner/repo` to assemble a site for another repository; publishing
still requires the account check in the script to pass.

The current bundles are Sandman-based development images. The v3.x community
build has not been validated on a physical recipient board. Do not use either
profile with real seeds or funds.
