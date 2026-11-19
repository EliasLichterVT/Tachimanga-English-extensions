
#!/usr/bin/env python3

import copy
import gzip
import json
import re
import sys
from pathlib import Path

UPSTREAM = Path(sys.argv[1])
OUTPUT = Path(sys.argv[2])

# Exact source-name aliases, normalized for matching.
ALLOWLIST = {
    "Asura Scans": [
        "Asura Scans",
        "AsuraComic",
        "Asura Comic",
    ],
    "Reaper Scans": [
        "Reaper Scans",
        "ReaperScans",
        "Reaper Comics",
    ],
    "MangaDex": [
        "MangaDex",
    ],
    "nhentai": [
        "nhentai",
    ],
    "E-Hentai / ExHentai": [
        "E-Hentai",
        "ExHentai",
        "E-Hentai / ExHentai",
    ],
    "Hitomi.la": [
        "Hitomi.la",
        "Hitomi",
    ],
    "Toonily": [
        "Toonily",
    ],
    "AsmHentai": [
        "AsmHentai",
    ],
}

def normalize(value):
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).lower()
    )

def find_group(entry):
    names = [
        entry.get("name", ""),
        entry.get("packageName", "").split(".")[-1],
    ]

    names.extend(
        source.get("name", "")
        for source in entry.get("sources", [])
    )

    normalized_names = {
        normalize(name)
        for name in names
    }

    for group, aliases in ALLOWLIST.items():
        if any(
            normalize(alias) in normalized_names
            for alias in aliases
        ):
            return group

    return None

def has_english(entry):
    return any(
        str(source.get("language", "")).lower()
        in ("en", "all")
        for source in entry.get("sources", [])
    )

def read_varint(data, position):
    value = 0
    shift = 0

    while True:
        if position >= len(data):
            raise ValueError("Truncated protobuf varint")

        byte = data[position]
        position += 1

        value |= (byte & 127) << shift

        if not (byte & 128):
            return value, position

        shift += 7

        if shift > 70:
            raise ValueError("Invalid protobuf varint")

def fields(data):
    position = 0

    while position < len(data):
        start = position
        tag, position = read_varint(
            data, position
        )

        number = tag >> 3
        wire = tag & 7
        payload = None

        if number == 0:
            raise ValueError("Invalid protobuf field")

        if wire == 0:
            _, position = read_varint(
                data, position
            )

        elif wire == 1:
            position += 8

        elif wire == 2:
            size, position = read_varint(
                data, position
            )
            payload = data[
                position:position + size
            ]
            position += size

        elif wire == 5:
            position += 4

        else:
            raise ValueError(
                f"Unsupported protobuf wire type: {wire}"
            )

        if position > len(data):
            raise ValueError(
                "Truncated protobuf field"
            )

        yield (
            number,
            wire,
            payload,
            data[start:position]
        )

def contains_package(data, packages, depth=0):
    """Find package strings in nested protobuf messages."""
    if depth > 8:
        return set()

    hits = set()

    try:
        parsed = list(fields(data))
    except ValueError:
        return hits

    for _, wire, payload, _ in parsed:
        if wire != 2 or payload is None:
            continue

        if payload in packages:
            hits.add(payload)
            continue

        # Inspect nested protobuf messages.
        hits.update(
            contains_package(
                payload,
                packages,
                depth + 1
            )
        )

    return hits

def filter_protobuf(original, packages):
    top = list(fields(original))

    # Determine which top-level field holds extension
    # records, using the selected package names.
    candidates = {}

    for number, wire, payload, _ in top:
        if wire != 2 or payload is None:
            continue

        hits = contains_package(
            payload, packages
        )

        if hits:
            candidates.setdefault(
                number, set()
            ).update(hits)

    valid = [
        number
        for number, hits in candidates.items()
        if hits == packages
    ]

    if len(valid) != 1:
        raise RuntimeError(
            "Could not safely identify protobuf "
            "extension records. No index published."
        )

    record_field = valid[0]
    kept = []
    matched = set()

    for number, wire, payload, raw in top:
        if number != record_field:
            kept.append(raw)
            continue

        if wire != 2 or payload is None:
            raise RuntimeError(
                "Unexpected extension record format"
            )

        hits = contains_package(
            payload, packages
        )

        if hits:
            kept.append(raw)
            matched.update(hits)

    if matched != packages:
        raise RuntimeError(
            "Protobuf package verification failed"
        )

    return b"".join(kept)

def main():
    OUTPUT.mkdir(
        parents=True,
        exist_ok=True
    )

    catalog = json.loads(
        (UPSTREAM / "index.json").read_text(
            encoding="utf-8"
        )
    )

    entries = catalog[
        "extensionList"
    ]["extensions"]

    selected = []
    found_groups = set()

    for entry in entries:
        group = find_group(entry)

        if not group or not has_english(entry):
            continue

        selected.append(entry)
        found_groups.add(group)

    if not selected:
        raise RuntimeError(
            "No requested extensions found"
        )

    packages = {
        entry["packageName"].encode("utf-8")
        for entry in selected
    }

    compressed = (
        UPSTREAM / "index.pb"
    ).read_bytes()

    if not compressed.startswith(b"\x1f\x8b"):
        raise RuntimeError(
            "Unexpected index.pb compression"
        )

    original = gzip.decompress(
        compressed
    )

    filtered = filter_protobuf(
        original,
        packages
    )

    # Preserve the catalog's original metadata.
    curated_catalog = copy.deepcopy(catalog)
    curated_catalog[
        "extensionList"
    ]["extensions"] = selected

    curated_catalog["name"] = (
        "Tachimanga Curated English"
    )

    repo = json.loads(
        (UPSTREAM / "repo.json").read_text(
            encoding="utf-8"
        )
    )

    # Retain upstream repository metadata.
    # APK URLs remain pointed at upstream releases.
    report = {
        "selected_count": len(selected),
        "found_groups": sorted(found_groups),
        "missing_groups": sorted(
            set(ALLOWLIST) - found_groups
        ),
        "extensions": [
            {
                "name": entry.get("name"),
                "packageName": entry.get(
                    "packageName"
                ),
                "contentWarning": entry.get(
                    "contentWarning"
                ),
                "apkUrl": entry.get(
                    "resources", {}
                ).get("apkUrl"),
            }
            for entry in selected
        ],
    }

    # Write only after validation succeeds.
    (OUTPUT / "index.pb").write_bytes(
        gzip.compress(
            filtered,
            mtime=0
        )
    )

    (OUTPUT / "index.json").write_text(
        json.dumps(
            curated_catalog,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    (OUTPUT / "repo.json").write_text(
        json.dumps(
            repo,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    (OUTPUT / "curated-report.json").write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    print(
        f"Selected {len(selected)} extensions"
    )

    print(
        "Included:",
        ", ".join(sorted(found_groups))
    )

    missing = report["missing_groups"]

    print(
        "Unavailable:",
        ", ".join(missing) if missing else "None"
    )

    print("Catalog generation complete.")

if __name__ == "__main__":
    main()
