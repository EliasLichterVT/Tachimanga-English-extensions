
#!/usr/bin/env python3
"""Build three independently curated Tachimanga catalogs."""

import copy
import gzip
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "catalogs.json"

BASE_URL = (
    "https://raw.githubusercontent.com/"
    "{repository}/{branch}/{filename}"
)


def download(repository, branch, filename):
    url = BASE_URL.format(
        repository=repository,
        branch=branch,
        filename=filename,
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Tachimanga-Catalog-Builder"},
    )

    with urllib.request.urlopen(
        request, timeout=90
    ) as response:
        return response.read()


def normalize(value):
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(value).lower(),
    )


def get_names(extension):
    names = [extension.get("name", "")]

    names.extend(
        source.get("name", "")
        for source in extension.get("sources", [])
    )

    return {normalize(name) for name in names}


def select_extensions(catalog, requested):
    available = catalog["extensionList"]["extensions"]
    selected = []
    found = set()

    for extension in available:
        names = get_names(extension)

        matches = [
            name
            for name in requested
            if normalize(name) in names
        ]

        if matches:
            selected.append(extension)
            found.update(matches)

    missing = sorted(set(requested) - found)

    if not selected:
        raise RuntimeError(
            "No matching extensions were found"
        )

    return selected, missing


def read_varint(data, position):
    result = 0
    shift = 0

    while True:
        if position >= len(data):
            raise ValueError("Truncated protobuf")

        byte = data[position]
        position += 1

        result |= (byte & 127) << shift

        if not (byte & 128):
            return result, position

        shift += 7

        if shift > 70:
            raise ValueError("Invalid varint")


def write_varint(value):
    output = bytearray()

    while value >= 128:
        output.append((value & 127) | 128)
        value >>= 7

    output.append(value)

    return bytes(output)


def parse_fields(data):
    position = 0
    fields = []

    while position < len(data):
        start = position
        tag, position = read_varint(data, position)

        number = tag >> 3
        wire = tag & 7
        payload = None

        if number == 0:
            raise ValueError("Invalid field number")

        if wire == 0:
            _, position = read_varint(data, position)

        elif wire == 1:
            position += 8

        elif wire == 2:
            length, position = read_varint(
                data, position
            )
            payload = data[position:position + length]
            position += length

        elif wire == 5:
            position += 4

        else:
            raise ValueError(
                f"Unsupported wire type {wire}"
            )

        if position > len(data):
            raise ValueError("Truncated field")

        fields.append(
            (number, wire, payload, data[start:position])
        )

    return fields


def package_hits(data, packages, depth=0):
    if depth > 12:
        return set()

    try:
        parsed = parse_fields(data)
    except ValueError:
        return set()

    hits = set()

    for _, wire, payload, _ in parsed:
        if wire != 2 or payload is None:
            continue

        if payload in packages:
            hits.add(payload)

        else:
            hits.update(
                package_hits(
                    payload, packages, depth + 1
                )
            )

    return hits


def filter_message(data, all_packages, selected, depth=0):
    if depth > 12:
        raise RuntimeError(
            "Protobuf nesting too deep"
        )

    parsed = parse_fields(data)
    groups = {}

    for index, (number, wire, payload, _) in enumerate(parsed):
        if wire != 2 or payload is None:
            continue

        hits = package_hits(payload, all_packages)

        if hits:
            groups.setdefault(number, []).append(
                (index, hits)
            )

    # Locate a repeated field containing individual
    # extension records. Each record must correspond
    # to exactly one known package.
    candidates = []

    for number, members in groups.items():
        if len(members) < 2:
            continue

        if not all(
            len(hits) == 1
            for _, hits in members
        ):
            continue

        union = set().union(
            *(hits for _, hits in members)
        )

        if union == all_packages:
            candidates.append((number, members))

    if len(candidates) > 1:
        raise RuntimeError(
            "Ambiguous protobuf extension fields"
        )

    if len(candidates) == 1:
        _, members = candidates[0]

        excluded = {
            index
            for index, hits in members
            if not (hits & selected)
        }

        kept = [
            raw
            for index, (_, _, _, raw)
            in enumerate(parsed)
            if index not in excluded
        ]

        return b"".join(kept), True

    # Search inside nested messages. We only modify
    # a nested field if its structure can be verified.
    output = []
    changed = False

    for number, wire, payload, raw in parsed:
        if wire != 2 or payload is None:
            output.append(raw)
            continue

        hits = package_hits(payload, all_packages)

        if not hits or changed:
            output.append(raw)
            continue

        try:
            updated, did_change = filter_message(
                payload,
                all_packages,
                selected,
                depth + 1,
            )
        except ValueError:
            output.append(raw)
            continue

        if did_change:
            tag = write_varint(
                (number << 3) | 2
            )

            output.append(
                tag
                + write_varint(len(updated))
                + updated
            )

            changed = True
        else:
            output.append(raw)

    return b"".join(output), changed


def filter_protobuf(data, all_names, selected_names):
    if not data.startswith(b"\x1f\x8b"):
        raise RuntimeError(
            "Expected gzip-compressed index.pb"
        )

    original = gzip.decompress(data)

    all_packages = {
        name.encode("utf-8")
        for name in all_names
    }

    selected = {
        name.encode("utf-8")
        for name in selected_names
    }

    if not selected <= all_packages:
        raise RuntimeError(
            "Selected packages missing upstream"
        )

    if len(selected) == len(all_packages):
        return data

    filtered, changed = filter_message(
        original,
        all_packages,
        selected,
    )

    if not changed:
        raise RuntimeError(
            "Could not locate protobuf extension "
            "records safely. Refusing to publish "
            "an unfiltered index."
        )

    remaining = package_hits(
        filtered, all_packages
    )

    if remaining != selected:
        raise RuntimeError(
            "Protobuf verification failed. "
            "Unexpected packages remain or "
            "selected packages are missing."
        )

    return gzip.compress(filtered, mtime=0)


def build_catalog(config):
    catalog_id = config["id"]
    repository = config["repository"]
    branch = config["branch"]

    print(f"\nBuilding {catalog_id}...", flush=True)

    catalog = json.loads(
        download(
            repository, branch, "index.json"
        ).decode("utf-8")
    )

    repo = json.loads(
        download(
            repository, branch, "repo.json"
        ).decode("utf-8")
    )

    protobuf = download(
        repository, branch, "index.pb"
    )

    all_extensions = catalog[
        "extensionList"
    ]["extensions"]

    selected, missing = select_extensions(
        catalog,
        config["extensions"],
    )

    all_packages = [
        item["packageName"]
        for item in all_extensions
    ]

    selected_packages = [
        item["packageName"]
        for item in selected
    ]

    if len(set(selected_packages)) != len(selected):
        raise RuntimeError(
            "Duplicate package names selected"
        )

    filtered_pb = filter_protobuf(
        protobuf,
        all_packages,
        selected_packages,
    )

    filtered_json = copy.deepcopy(catalog)
    filtered_json["name"] = config["name"]
    filtered_json[
        "extensionList"
    ]["extensions"] = selected

    output_dir = ROOT / catalog_id
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Preserve the upstream signing fingerprint.
    # Each catalog remains an independent entity.
    repo.setdefault("meta", {})
    repo["meta"]["name"] = config["name"]

    repo["index_v2"] = (
        "https://raw.githubusercontent.com/"
        "EliasLichterVT/"
        "Tachimanga-English-extensions/"
        f"main/{catalog_id}/index.pb"
    )

    report = {
        "catalog": catalog_id,
        "upstream": repository,
        "selected_count": len(selected),
        "missing": missing,
        "extensions": [
            {
                "name": item["name"],
                "packageName": item["packageName"],
                "apkUrl": item.get(
                    "resources", {}
                ).get("apkUrl"),
            }
            for item in selected
        ],
    }

    (output_dir / "index.pb").write_bytes(
        filtered_pb
    )

    (output_dir / "index.json").write_text(
        json.dumps(
            filtered_json,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    (output_dir / "repo.json").write_text(
        json.dumps(
            repo,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    (output_dir / "report.json").write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )

    print(
        f"{catalog_id}: "
        f"{len(selected)} extensions selected"
    )

    if missing:
        print(
            "Missing: " + ", ".join(missing)
        )

    return set(selected_packages)


def main():
    configuration = json.loads(
        CONFIG.read_text(encoding="utf-8")
    )

    used_packages = set()

    for config in configuration["catalogs"]:
        packages = build_catalog(config)

        collisions = packages & used_packages

        if collisions:
            raise RuntimeError(
                "Package collision between catalogs: "
                + ", ".join(sorted(collisions))
            )

        used_packages.update(packages)

    print("\nAll catalogs built successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            f"BUILD FAILED: {error}",
            file=sys.stderr,
        )
        sys.exit(1)
