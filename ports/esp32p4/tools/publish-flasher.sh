#!/usr/bin/env bash
# Assemble and optionally publish the revision-aware ESP32-P4 Web Flasher.
#
# Usage:
#   tools/publish-flasher.sh [--pre-rev3-tag TAG] [--rev3-zip FILE.zip]
#       [--rev3-tag TAG] [--rev3-url URL] [--dry-run]
#
# The pre-rev3 bundle is downloaded from Sandman's ESP32-P4 release. The
# existing v3.x community bundle is retained from the Pages branch unless a
# replacement ZIP is supplied. No official Specter bootloader is involved;
# the page writes the ESP32-P4 ROM-bootloader images over the UART bridge.

set -euo pipefail

P4_DIR="$(cd "$(dirname -- "$0")/.." && pwd)"

PAGES_REPO="${PAGES_REPO:-schnuartz-ai/specter-diy}"
FIRMWARE_REPO="${FIRMWARE_REPO:-sandman21vs/specter-diy}"
PRE_REV3_TAG=""
REV3_ZIP=""
REV3_TAG=""
REV3_URL=""
REV3_SOURCE_COMMIT=""
REV3_SOURCE_URL=""
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --pre-rev3-tag) PRE_REV3_TAG="${2:?missing tag}"; shift 2 ;;
    --rev3-zip) REV3_ZIP="${2:?missing ZIP path}"; shift 2 ;;
    --rev3-tag) REV3_TAG="${2:?missing tag}"; shift 2 ;;
    --rev3-url) REV3_URL="${2:?missing URL}"; shift 2 ;;
    --rev3-source-commit) REV3_SOURCE_COMMIT="${2:?missing commit}"; shift 2 ;;
    --rev3-source-url) REV3_SOURCE_URL="${2:?missing source URL}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '1,18p' "$0"
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

for repo in "$PAGES_REPO" "$FIRMWARE_REPO"; do
  [[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || {
    echo "invalid GitHub repository name: $repo" >&2
    exit 2
  }
done

command -v gh >/dev/null || { echo "gh is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }

if [ -z "$PRE_REV3_TAG" ]; then
  PRE_REV3_TAG="$(gh release list -R "$FIRMWARE_REPO" --limit 50 \
    --json tagName,publishedAt \
    --jq '[.[] | select(.tagName | startswith("esp32p4-"))] | sort_by(.publishedAt) | last | .tagName')"
fi
if [ -z "$PRE_REV3_TAG" ] || [ "$PRE_REV3_TAG" = "null" ]; then
  echo "no esp32p4-* release found in $FIRMWARE_REPO" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
SITE="$WORK/site"
PAGES_URL="https://github.com/${PAGES_REPO}.git"
FIRMWARE_DIR="$SITE/firmware/wave_43"

echo "==> Loading current Pages site from $PAGES_REPO"
git clone -q --depth 1 --branch gh-pages "$PAGES_URL" "$SITE"
cp -R "$P4_DIR/flasher/." "$SITE/"
touch "$SITE/.nojekyll"
mkdir -p "$FIRMWARE_DIR"

echo "==> Downloading pre-rev3 firmware: $FIRMWARE_REPO $PRE_REV3_TAG"
mkdir -p "$WORK/pre-rev3-download"
gh release download "$PRE_REV3_TAG" -R "$FIRMWARE_REPO" \
  -p 'specter-diy-wave_43-*.zip' -D "$WORK/pre-rev3-download"
shopt -s nullglob
PRE_REV3_ZIPS=("$WORK/pre-rev3-download"/specter-diy-wave_43-*.zip)
if [ "${#PRE_REV3_ZIPS[@]}" -ne 1 ]; then
  echo "expected exactly one specter-diy-wave_43-*.zip asset for $PRE_REV3_TAG" >&2
  exit 1
fi
PRE_TAG="$(gh release view "$PRE_REV3_TAG" -R "$FIRMWARE_REPO" \
  --json tagName --jq .tagName)"
PRE_URL="$(gh release view "$PRE_REV3_TAG" -R "$FIRMWARE_REPO" \
  --json url --jq .url)"
PRE_PUBLISHED="$(gh release view "$PRE_REV3_TAG" -R "$FIRMWARE_REPO" \
  --json publishedAt --jq .publishedAt)"

mkdir -p "$WORK/pre-rev3-profile"
python3 "$P4_DIR/tools/package_flasher_profile.py" \
  --zip "${PRE_REV3_ZIPS[0]}" \
  --output-dir "$WORK/pre-rev3-profile" \
  --profile pre-rev3 \
  --tag "$PRE_TAG" \
  --url "$PRE_URL" \
  --published "$PRE_PUBLISHED"

replace_profile() {
  local profile="$1"
  local staged="$2"
  local destination="$FIRMWARE_DIR/$profile"
  local backup="$WORK/old-$profile"
  if [ -d "$destination" ]; then
    mv "$destination" "$backup"
  fi
  mv "$staged" "$destination"
}

replace_profile pre-rev3 "$WORK/pre-rev3-profile"

if [ -n "$REV3_ZIP" ]; then
  [ -f "$REV3_ZIP" ] || { echo "rev3 ZIP not found: $REV3_ZIP" >&2; exit 1; }
  REV3_TAG="${REV3_TAG:-$(basename "${REV3_ZIP%.zip}")}"
  REV3_URL="${REV3_URL:-https://github.com/${PAGES_REPO}/releases}"
  mkdir -p "$WORK/rev3-profile"
  python3 "$P4_DIR/tools/package_flasher_profile.py" \
    --zip "$REV3_ZIP" \
    --output-dir "$WORK/rev3-profile" \
    --profile rev3 \
    --tag "$REV3_TAG" \
    --url "$REV3_URL" \
    --source-commit "$REV3_SOURCE_COMMIT" \
    --source-url "$REV3_SOURCE_URL"
  replace_profile rev3 "$WORK/rev3-profile"
elif [ ! -f "$FIRMWARE_DIR/rev3/release.json" ]; then
  echo "no existing rev3 bundle found; supply --rev3-zip to publish a v3.x profile" >&2
  exit 1
fi

if [ "$DRY_RUN" -eq 1 ]; then
  mkdir -p "$P4_DIR/deps"
  PREVIEW="$(mktemp -d "$P4_DIR/deps/flasher-site.XXXXXX")"
  cp -R "$SITE/." "$PREVIEW/"
  echo "==> Dry run complete. Site assembled at $PREVIEW"
  exit 0
fi

git -C "$SITE" config user.name "Schnuartz AI"
git -C "$SITE" config user.email "schnuartz-ai@users.noreply.github.com"
git -C "$SITE" add -A
if git -C "$SITE" diff --cached --quiet; then
  echo "==> No changes to publish."
  exit 0
fi
git -C "$SITE" commit -m "Publish ESP32-P4 flasher for $PRE_REV3_TAG"

# Public Pages updates are restricted to the authorized non-main account.
GH_LOGIN="$(gh api user --jq .login)"
if [ "$GH_LOGIN" != "schnuartz-ai" ]; then
  echo "Refusing to publish: active GitHub account is '$GH_LOGIN', expected 'schnuartz-ai'." >&2
  exit 1
fi

echo "==> Publishing to $PAGES_REPO:gh-pages"
git -C "$SITE" -c credential.helper= \
  -c credential.helper='!gh auth git-credential' \
  push origin HEAD:gh-pages

OWNER="${PAGES_REPO%%/*}"
NAME="${PAGES_REPO#*/}"
echo "==> Published: https://${OWNER}.github.io/${NAME}/"
