#!/usr/bin/env python3
"""Prepare one revision-specific bundle for the ESP32-P4 web flasher."""

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


APP_IMAGES = {"specter-diy.bin", "micropython.bin"}
STORAGE_RESET = b"\xff" * 8192
FLASH_FILES = {
    0x2000: "bootloader.bin",
    0x8000: "partition-table.bin",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def zip_member_name(name):
    if "\\" in name:
        raise ValueError(f"Invalid ZIP path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid ZIP path: {name}")
    return path


def find_member(archive, basename):
    matches = []
    for info in archive.infolist():
        path = zip_member_name(info.filename)
        if not info.is_dir() and path.name == basename:
            matches.append(info)
    if len(matches) != 1:
        raise ValueError(f"Expected one {basename} in firmware ZIP; found {len(matches)}")
    return matches[0]


def flash_layout(archive):
    args_info = find_member(archive, "flasher_args.json")
    args = json.loads(archive.read(args_info).decode("utf-8"))
    files = args.get("flash_files")
    if not isinstance(files, dict):
        raise ValueError("flasher_args.json has no flash_files map")

    layout = {}
    for offset_text, filename in files.items():
        offset = int(offset_text, 0) if isinstance(offset_text, str) else int(offset_text)
        name = zip_member_name(filename).name
        if offset in layout:
            raise ValueError(f"Duplicate flash offset: {offset_text}")
        layout[offset] = name

    for offset, expected_name in FLASH_FILES.items():
        if layout.get(offset) != expected_name:
            raise ValueError(f"Expected {expected_name} at {offset:#x}")
    app_name = layout.get(0x10000)
    if app_name not in APP_IMAGES:
        raise ValueError("Expected specter-diy.bin or micropython.bin at 0x10000")

    allowed_offsets = {0x2000, 0x8000, 0x10000, 0x410000}
    unexpected = set(layout) - allowed_offsets
    if unexpected:
        raise ValueError("Unexpected flash offsets: " + ", ".join(hex(x) for x in sorted(unexpected)))

    return args, app_name


def write_deterministic_zip(output_path, names, directory):
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in names:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, (directory / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path, help="input firmware bundle")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--profile", required=True, choices=("pre-rev3", "rev3"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--published", default="")
    parser.add_argument("--zip-name", default="")
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--source-url", default="")
    args = parser.parse_args()

    if not args.zip.is_file():
        parser.error(f"firmware ZIP does not exist: {args.zip}")
    zip_name = args.zip_name or args.zip.name
    if Path(zip_name).name != zip_name or not zip_name.lower().endswith(".zip"):
        parser.error("--zip-name must be a simple .zip filename")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(args.zip) as archive:
        source_args, app_name = flash_layout(archive)
        names = ["bootloader.bin", "partition-table.bin", app_name]
        data = {name: archive.read(find_member(archive, name)) for name in names}

    if len(data["bootloader.bin"]) > 0x6000:
        parser.error("bootloader image overlaps the partition table")
    if len(data["partition-table.bin"]) > 0x1000:
        parser.error("partition table exceeds its 4 KiB flash sector")
    if len(data[app_name]) > 0x400000:
        parser.error("application image exceeds the 4 MiB factory partition")

    for name, contents in data.items():
        (output_dir / name).write_bytes(contents)
    (output_dir / "storage-reset.bin").write_bytes(STORAGE_RESET)

    normalized_args = {
        "flash_settings": source_args.get(
            "flash_settings",
            {"flash_mode": "dio", "flash_size": "16MB", "flash_freq": "40m"},
        ),
        "flash_files": {
            "0x2000": "bootloader.bin",
            "0x8000": "partition-table.bin",
            "0x10000": app_name,
            "0x410000": "storage-reset.bin",
        },
    }
    (output_dir / "flasher_args.json").write_text(
        json.dumps(normalized_args, indent=2) + "\n", encoding="utf-8"
    )

    names = ["bootloader.bin", "partition-table.bin", app_name, "storage-reset.bin", "flasher_args.json"]
    write_deterministic_zip(output_dir / zip_name, names, output_dir)

    published = args.published or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    metadata = {
        "tag": args.tag,
        "tagName": args.tag,
        "url": args.url,
        "published": published,
        "publishedAt": published,
        "profile": args.profile,
        "zip": zip_name,
        "sha256": {name: sha256(output_dir / name) for name in names if name != "flasher_args.json"},
        "zip_sha256": sha256(output_dir / zip_name),
    }
    if args.source_commit:
        metadata["source_commit"] = args.source_commit
    if args.source_url:
        metadata["source_url"] = args.source_url
    (output_dir / "release.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    sums = [f"{metadata['sha256'][name]}  {name}" for name in names if name != "flasher_args.json"]
    sums.append(f"{metadata['zip_sha256']}  {zip_name}")
    (output_dir / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", encoding="ascii")


if __name__ == "__main__":
    main()
