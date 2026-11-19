
#!/usr/bin/env python3

import gzip
import json
import re
import sys
from pathlib import Path

UPSTREAM = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2])

REPO_OWNER = "EliasLichterVT"
REPO_NAME = "Tachimanga-English-extensions"

INDEX_URL = (
    f"https://raw.githubusercontent.com/"
    f"{REPO_OWNER}/{REPO_NAME}/main/index.pb"
)

# These are source-name aliases, not package IDs.
# Only extensions actually found upstream are included.
ALLOWLIST = {
    "Asura Scans": [
        "asura scans",
        "asurascans",
        "asuracomic",
        "asura comic",
    ],
    "Reaper Scans": [
        "reaper scans",
        "reaperscans",
        "reaper comics",
    ],
    "MangaDex": [
        "mangadex",
    ],
    "nhentai": [
        "nhentai",
    ],
    "E-Hentai / ExHentai": [
        "e-hentai",
        "exhentai",
    ],
    "Hitomi.la": [
        "hitomi.la",
        "hitomi",
    ],
    "Toonily": [
        "toonily",
    ],
}

def normalize(value):
    return re.sub(
        r"[^a-z0-9]+", "",
        str(value).lower()
    )

def matches(entry):
    names = [entry.get("name", "")]
    names.extend(
        source.get("name", "")
        for source in entry.get("sources", [])
    )

    for group, aliases in ALLOWLIST.items():
        for name in names:
            normalized = normalize(name)
            if any(
                normalize(alias) == normalized
                for alias in aliases
            ):
                return group

    return None

def read_varint(data, pos):
    result = 0
    shift = 0

    while True:
        if pos >= len(data):
            raise ValueError("Truncated protobuf")

        byte = data[pos]
        pos += 1

        result |= (byte & 0x7F) << shift

        if not byte & 0x80:
            return result, pos

        shift += 7

        if shift > 70:
            raise ValueError("Invalid varint")

def protobuf_fields(data):
    """Yield complete encoded protobuf fields."""
    pos = 0

    while pos < len(data):
        start = pos
        tag, pos = read_varint(data, pos)
        field_number = tag >> 3
        wire_type = tag & 7
        payload = None

        if wire_type == 0:
            _, pos = read_varint(data, pos)
        elif wire_type == 1:
            pos += 8
        elif wire_type == 2:
            length, pos = read_varint(data, pos)
            payload = data[pos:pos + length]
            pos += length
        elif wire_type == 5:
            pos += 4
        else:
            raise ValueError(
                f"Unsupported wire type: {wire_type}"
            )

        if pos > len(data):
            raise ValueError("Truncated protobuf field")

        yield field_number, wire_type, payload, data[start:pos]

def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)

    entries = json.loads(
        (UPSTREAM / "index.json").read_text()
    )

    if not isinstance(entries, list):
        raise ValueError("Unexpected JSON catalog format")

    selected = []
    found_groups = set()

    for entry in entries:
        if entry.get("lang", "").lower() != "en":
            continue

        group = matches(entry)

        if group:
            selected.append(entry)
            found_groups.add(group)

    if not selected:
        raise RuntimeError(
            "No matching English extensions found"
        )

    packages = {
        entry["pkg"].encode("utf-8")
        for entry in selected
    }

    compressed = (UPSTREAM / "index.pb").read_bytes()

    if not compressed.startswith(b"\x1f\x8b"):
        raise RuntimeError(
            "Expected a gzip-compressed protobuf index"
        )

    original = gzip.decompress(compressed)

    retained = []
    matched_packages = set()

    for number, wire_type, payload, raw in protobuf_fields(original):
        if wire_type != 2 or payload is None:
            retained.append(raw)
            continue

        # Match package IDs as complete protobuf string
        # fields inside each top-level extension record.
        strings = {
            value
            for _, kind, value, _ in protobuf_fields(payload)
            if kind == 2 and value is not None
        }

        hits = strings & packages

        if hits:
            retained.append(raw)
            matched_packages.update(hits)
        elif number != 1:
            # Preserve top-level metadata fields.
            retained.append(raw)

    if matched_packages != packages:
        missing = sorted(
            pkg.decode("utf-8")
            for pkg in packages - matched_packages
        )
        raise RuntimeError(
            "Protobuf entries did not match JSON catalog. "
            "No files published. Missing: "
            + ", ".join(missing)
        )

    filtered = b"".join(retained)

    (OUTPUT / "index.pb").write_bytes(
        gzip.compress(filtered, mtime=0)
    )

    (OUTPUT / "index.json").write_text(
        json.dumps(
            selected,
            indent=2,
            ensure_ascii=False
        ) + "\n"
    )

    upstream_repo = json.loads(
        (UPSTREAM / "repo.json").read_text()
    )

    upstream_repo["index_v2"] = INDEX_URL
    upstream_repo.setdefault("meta", {})["name"] = (
        "Tachimanga Curated English"
    )

    (OUTPUT / "repo.json").write_text(
        json.dumps(upstream_repo, indent=2) + "\n"
    )

    report = {
        "selected_extensions": len(selected),
        "found_groups": sorted(found_groups),
        "missing_groups": sorted(
            set(ALLOWLIST) - found_groups
        ),
        "extensions": [
            {
                "name": entry["name"],
                "pkg": entry["pkg"],
                "nsfw": entry.get("nsfw"),
            }
            for entry in selected
        ],
    }

    (OUTPUT / "curated-report.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )

    print(
        f"Selected {len(selected)} English extensions"
    )
    print("Found:", ", ".join(sorted(found_groups)))
    print(
        "Missing:",
        ", ".join(report["missing_groups"]) or "None"
    )

if __name__ == "__main__":
    main()
