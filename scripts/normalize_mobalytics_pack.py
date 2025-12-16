#!/usr/bin/env python3
"""
Normalize Mobalytics data.json into our schema-shaped pack files.

Input:
  data/<set_patch>/raw/data.json

Output (overwritten):
  data/<set_patch>/pack.json
  data/<set_patch>/traits.json
  data/<set_patch>/items.json
  data/<set_patch>/champions.json

Also writes:
  data/<set_patch>/raw/id_map.json  (raw slug -> normalized id)
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple, Set


# --- helpers ---------------------------------------------------------------

_ID_RE = re.compile(r"[^a-z0-9]+")

def slug_to_id(slug: str) -> str:
    """Convert hyphenated/odd slugs into schema-safe snake_case ids."""
    s = (slug or "").strip().lower()
    s = _ID_RE.sub("_", s).strip("_")
    # collapse multiple underscores
    s = re.sub(r"_+", "_", s)
    if not s:
        raise ValueError(f"Cannot normalize empty slug: {slug!r}")
    return s[:64]


def now_utc_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def pick_item_effect_tags_from_bonus_stats(bonus_stats: List[Dict[str, Any]], *, fallback: str) -> List[str]:
    """
    Translate Mobalytics bonusStats slugs to our coarse effect_tags enum.
    We keep this intentionally simple; you can refine later.
    """
    tags: Set[str] = set()

    for bs in bonus_stats or []:
        slug = (bs.get("slug") or "").lower()
        # speed
        if "attack-speed" in slug or "attack_speed" in slug:
            tags.add("attack_speed")
        # mana
        if "mana" in slug:
            tags.add("mana")
        # tanky stats
        if any(k in slug for k in ["health", "armor", "magic-resist", "mr", "resist"]):
            tags.add("tank")
        # damage stats (AD/AP/crit etc. all become "damage" at this layer)
        if any(k in slug for k in ["attack-damage", "spell-damage", "crit", "critical", "damage"]):
            tags.add("damage")

    if not tags:
        tags.add(fallback)

    # keep deterministic order
    return sorted(tags)


def infer_kind(components: List[str], base_components: Set[str], raw_name: str) -> str:
    """
    kind ∈ {component, completed, artifact}
    """
    if len(components) == 2:
        return "completed"
    if len(components) == 0:
        # Base components are known via being referenced in buildsFrom
        if slug_to_id(raw_name) in base_components:
            return "component"
        # Spatula-like bases are also components; base_components already captures them by slug.
        # Everything else that's non-craftable: treat as artifact (includes emblems/unique drops).
        return "artifact"
    # Unexpected, but treat as artifact to avoid schema failure
    return "artifact"


def intended_role_from_tags(effect_tags: List[str], name: str, kind: str) -> str:
    name_l = (name or "").lower()
    if "tank" in effect_tags:
        return "tank"
    if kind == "artifact" and "emblem" in name_l:
        return "support"
    if "antiheal" in effect_tags or "shred" in effect_tags or "cleanse" in effect_tags or "cc" in effect_tags:
        return "support"
    # default
    return "carry"


# --- normalization ----------------------------------------------------------

def normalize_traits(raw_synergies: List[Dict[str, Any]], id_map: Dict[str, str]) -> List[Dict[str, Any]]:
    traits: List[Dict[str, Any]] = []
    for s in raw_synergies:
        fd = s.get("flatData") or {}
        raw_slug = fd.get("slug")
        if not raw_slug:
            continue
        tid = id_map.setdefault(raw_slug, slug_to_id(raw_slug))
        traits.append({
            "id": tid,
            "name": fd.get("name") or raw_slug,
            # breakpoints often not present in Mobalytics data.json; fill later if needed
            "breakpoints": [],
            "notes": fd.get("type") or ""
        })
    # stable sort
    traits.sort(key=lambda x: x["id"])
    return traits


def normalize_items(raw_items: List[Dict[str, Any]], id_map: Dict[str, str]) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """
    Returns:
      (normalized_items, base_component_ids)
    """
    # Identify base components by scanning all buildsFrom references
    referenced_slugs: Set[str] = set()
    for it in raw_items:
        fd = it.get("flatData") or {}
        for bf in fd.get("buildsFrom") or []:
            bffd = bf.get("flatData") or {}
            if bffd.get("slug"):
                referenced_slugs.add(bffd["slug"])

    base_component_ids: Set[str] = {id_map.setdefault(s, slug_to_id(s)) for s in referenced_slugs}

    out: List[Dict[str, Any]] = []

    for it in raw_items:
        fd = it.get("flatData") or {}
        raw_slug = fd.get("slug")
        if not raw_slug:
            continue
        iid = id_map.setdefault(raw_slug, slug_to_id(raw_slug))

        builds_from = fd.get("buildsFrom") or []
        component_ids: List[str] = []
        for bf in builds_from:
            bffd = bf.get("flatData") or {}
            bslug = bffd.get("slug")
            if bslug:
                component_ids.append(id_map.setdefault(bslug, slug_to_id(bslug)))

        kind = "completed" if len(component_ids) == 2 else ("component" if iid in base_component_ids else "artifact")

        # effect tags: minimal but schema-safe; refine later with your tag vocab
        effect_tags = pick_item_effect_tags_from_bonus_stats(
            fd.get("bonusStats") or [],
            fallback=("artifact" if kind == "artifact" else "damage")
        )

        # ensure components rule: components empty for component/artifact
        if kind in ("component", "artifact"):
            component_ids = []

        out.append({
            "id": iid,
            "name": fd.get("name") or raw_slug,
            "kind": kind,
            "components": component_ids,
            "effect_tags": effect_tags,
            "intended_role": intended_role_from_tags(effect_tags, fd.get("name") or "", kind),
            "grants": {
                "utilities": [],   # fill later (antiheal/shred/etc.)
                "cc": False,
                "cleanse": False
            },
            "recommended_scaling": [],
            "bad_for_scaling": [],
            "unique": False
        })

    out.sort(key=lambda x: x["id"])
    return out, base_component_ids


def normalize_champions(raw_champions: List[Dict[str, Any]], id_map: Dict[str, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    for c in raw_champions:
        fd = c.get("flatData") or {}
        raw_slug = fd.get("slug")
        if not raw_slug:
            continue
        cid = id_map.setdefault(raw_slug, slug_to_id(raw_slug))

        # synergies: list of { flatData: { slug, name, ... } }
        traits: List[str] = []
        for syn in fd.get("synergies") or []:
            sfd = syn.get("flatData") or {}
            sslug = sfd.get("slug")
            if sslug:
                traits.append(id_map.setdefault(sslug, slug_to_id(sslug)))
        traits = sorted(set(traits))

        # schema requires at least 1 role tag; default to flex until curated
        out.append({
            "id": cid,
            "name": fd.get("name") or raw_slug,
            "cost": int(fd.get("cost") or 1),
            "traits": traits,
            "tags": {
                "roles": ["flex"],
                "scaling": [],
                "patterns": [],
                "utility_sources": [],
                "constraints": []
            },
            "preferred_item_tags": [],
            "positioning": "mid",
            "notes": {
                "synergy": ""
            }
        })

    out.sort(key=lambda x: x["id"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="Pack folder, e.g. data/set16_16.1")
    ap.add_argument("--set", dest="set_name", default="set16", help="Set label for pack.json")
    ap.add_argument("--patch", dest="patch", default="16.1", help="Patch label for pack.json")
    args = ap.parse_args()

    pack_dir = Path(args.pack)
    raw_path = pack_dir / "raw" / "data.json"
    if not raw_path.exists():
        raise SystemExit(f"Missing raw data.json at: {raw_path}")

    raw = read_json(raw_path)
    root = raw.get("data") or {}
    raw_items = root.get("items") or []
    raw_champions = root.get("champions") or []
    raw_synergies = root.get("synergies") or []

    id_map: Dict[str, str] = {}

    traits = normalize_traits(raw_synergies, id_map)
    items, base_components = normalize_items(raw_items, id_map)
    champions = normalize_champions(raw_champions, id_map)

    # Write outputs (schema-shaped)
    write_json(pack_dir / "pack.json", {
        "pack_id": slug_to_id(f"{args.set_name}_{args.patch}".replace(".", "_")),
        "game": "tft",
        "set": args.set_name,
        "patch": args.patch,
        "version": "1.0",
        "created_utc": now_utc_iso_z(),
        "notes": "Normalized from Mobalytics data.json (raw kept in /raw)."
    })

    write_json(pack_dir / "traits.json", {
        "version": "1.0",
        "traits": traits
    })

    write_json(pack_dir / "items.json", {
        "version": "1.0",
        "items": items
    })

    write_json(pack_dir / "champions.json", {
        "version": "1.0",
        "champions": champions
    })

    # Helpful mapping file for debugging/template-writing
    write_json(pack_dir / "raw" / "id_map.json", {
        "raw_slug_to_id": dict(sorted(id_map.items(), key=lambda kv: kv[0])),
        "base_component_ids": sorted(base_components)
    })

    print(f"✅ Normalized pack written to: {pack_dir}")
    print(f"   Traits: {len(traits)} | Champions: {len(champions)} | Items: {len(items)}")
    print(f"   Base components detected: {len(base_components)}")


if __name__ == "__main__":
    main()
