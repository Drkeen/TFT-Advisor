#!/usr/bin/env python3
"""
Validate a normalized pack:
- JSON Schema validation
- Cross-file checks (recipes reference existing components, etc.)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from jsonschema import validate  # pip install jsonschema


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_schema(schema_path: Path) -> Dict[str, Any]:
    return read_json(schema_path)


def schema_validate(data: Any, schema: Dict[str, Any], label: str) -> None:
    validate(instance=data, schema=schema)
    print(f"✅ Schema OK: {label}")


def cross_file_checks(pack_dir: Path) -> None:
    champs = read_json(pack_dir / "champions.json")["champions"]
    items = read_json(pack_dir / "items.json")["items"]
    traits = read_json(pack_dir / "traits.json")["traits"]

    champ_ids = {c["id"] for c in champs}
    item_ids = {i["id"] for i in items}
    trait_ids = {t["id"] for t in traits}

    # 1) champion traits exist
    missing_traits = []
    for c in champs:
        for tid in c.get("traits", []):
            if tid not in trait_ids:
                missing_traits.append((c["id"], tid))
    if missing_traits:
        raise ValueError(f"Missing trait ids referenced by champions: {missing_traits[:20]} ...")

    # 2) completed items have 2 component ids and they exist
    missing_components = []
    bad_component_counts = []
    component_like = set()
    for it in items:
        if it["kind"] == "component":
            component_like.add(it["id"])

    for it in items:
        if it["kind"] == "completed":
            comps = it.get("components", [])
            if len(comps) != 2:
                bad_component_counts.append((it["id"], len(comps)))
            for cid in comps:
                if cid not in item_ids:
                    missing_components.append((it["id"], cid))
                else:
                    # optionally enforce: components must be kind=component
                    # (if you want strictness early, uncomment)
                    pass

    if bad_component_counts:
        raise ValueError(f"Completed items with non-2 components: {bad_component_counts[:20]} ...")
    if missing_components:
        raise ValueError(f"Items referencing missing component ids: {missing_components[:20]} ...")

    print("✅ Cross-file checks OK")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="Pack folder, e.g. data/set16_16.1")
    ap.add_argument("--schemas", required=True, help="Folder containing *.schema.json files")
    args = ap.parse_args()

    pack_dir = Path(args.pack)
    schema_dir = Path(args.schemas)

    pack = read_json(pack_dir / "pack.json")
    champions = read_json(pack_dir / "champions.json")
    items = read_json(pack_dir / "items.json")
    traits = read_json(pack_dir / "traits.json")

    schema_validate(pack, load_schema(schema_dir / "pack.schema.json"), "pack.json")
    schema_validate(champions, load_schema(schema_dir / "champions.schema.json"), "champions.json")
    schema_validate(items, load_schema(schema_dir / "items.schema.json"), "items.json")
    schema_validate(traits, load_schema(schema_dir / "traits.schema.json"), "traits.json")

    cross_file_checks(pack_dir)


if __name__ == "__main__":
    main()
