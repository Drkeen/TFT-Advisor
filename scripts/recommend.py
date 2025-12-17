#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from tft_advisor.recommender import read_json, recommend


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="e.g. data/set16_16.1")
    ap.add_argument("--templates", required=True, help="Template file OR folder, e.g. templates/set16_16.1/builds")
    ap.add_argument("--gamestate", required=True, help="e.g. examples/gamestate_samples/ekkoroll_4-1_level7.json")
    args = ap.parse_args()

    pack_dir = Path(args.pack)
    templates_path = Path(args.templates)
    gs_path = Path(args.gamestate)

    gs = read_json(gs_path)
    out = recommend(pack_dir, templates_path, gs)
    out["meta"]["generated_from"]["gamestate"] = str(gs_path).replace("\\", "/")

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
