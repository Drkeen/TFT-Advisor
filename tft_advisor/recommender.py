from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional


# ---------- IO ----------
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_pack(pack_dir: Path) -> Dict[str, Any]:
    champs = read_json(pack_dir / "champions.json")["champions"]
    items = read_json(pack_dir / "items.json")["items"]
    traits = read_json(pack_dir / "traits.json")["traits"]
    return {
        "champions": {c["id"]: c for c in champs},
        "items": {i["id"]: i for i in items},
        "traits": {t["id"]: t for t in traits},
    }


def load_templates(path: Path) -> List[Dict[str, Any]]:
    if path.is_file():
        return [read_json(path)]
    if path.is_dir():
        return [read_json(p) for p in sorted(path.glob("*.json"))]
    raise FileNotFoundError(str(path))


# ---------- Helpers ----------
def stage_to_int(stage: str) -> int:
    try:
        a, b = stage.split("-")
        return int(a) * 10 + int(b)
    except Exception:
        return 0


def count_traits(pack: Dict[str, Any], unit_ids: List[str]) -> Counter:
    ctr = Counter()
    for uid in unit_ids:
        champ = pack["champions"].get(uid)
        if not champ:
            continue
        for tid in champ.get("traits", []):
            ctr[tid] += 1
    return ctr


def craftable_items(pack: Dict[str, Any], inventory: List[str]) -> List[str]:
    inv = Counter(inventory)
    out = []
    for it in pack["items"].values():
        if it.get("kind") != "completed":
            continue
        comps = it.get("components", [])
        if len(comps) != 2:
            continue
        need = Counter(comps)
        if all(inv[c] >= need[c] for c in need):
            out.append(it["id"])
    return sorted(out)


def desired_items_index(template: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    items_block = template.get("items", {})
    for holder_id, plan in items_block.items():
        if not isinstance(plan, dict):
            continue
        ids = plan.get("items") or []
        if not isinstance(ids, list):
            continue
        for idx, item_id in enumerate(ids):
            if not isinstance(item_id, str):
                continue
            out[item_id] = {
                "final_holder": holder_id,
                "priority_index": idx,
                "is_core": idx < 2
            }
    return out


def normalize_then(then_val: Any) -> Dict[str, Any]:
    if isinstance(then_val, dict):
        return then_val
    if isinstance(then_val, str):
        if ":" in then_val:
            tag, msg = then_val.split(":", 1)
            tag = tag.strip()
            msg = msg.strip()
            if tag == "pivot_to_backup_void":
                return {"action": "switch_template", "target": "backup_void", "why": msg or "Pivot to backup."}
            if tag in {"hard_pivot", "soft_pivot_warning"}:
                return {"action": "consider_templates", "targets": ["backup_void", "greedy_fast8"], "why": msg or "Consider pivot."}
            if tag == "convert_lead":
                return {"action": "set_policy", "policy": "push_levels", "why": msg or "Convert lead by leveling."}
            if tag == "stop_greed":
                return {"action": "set_policy", "policy": "stabilize_now", "why": msg or "Stabilize now."}
            return {"action": "note", "tag": tag, "message": msg}
        return {"action": "note", "message": then_val}
    return {"action": "note", "message": str(then_val)}


def unit_stars(gs: Dict[str, Any], champion_id: str) -> int:
    best = 0
    for u in gs.get("board", []):
        if u.get("champion_id") == champion_id:
            best = max(best, int(u.get("stars", 1)))
    for u in gs.get("bench", []):
        if u.get("champion_id") == champion_id:
            best = max(best, int(u.get("stars", 1)))
    return best


def parse_miss_token(token: str) -> Tuple[str, Optional[int]]:
    if "_" in token:
        champ, maybe_num = token.rsplit("_", 1)
        try:
            return champ, int(maybe_num)
        except ValueError:
            return token, None
    return token, None


# ---------- Core decision logic (yours) ----------
def choose_now_holder(pack: Dict[str, Any], gs: Dict[str, Any], template: Dict[str, Any], item_id: str, final_holder: str) -> str:
    board_ids = [u["champion_id"] for u in gs["board"]]
    if final_holder in board_ids:
        return final_holder

    item = pack["items"].get(item_id, {})
    tags = set(item.get("effect_tags", []) or [])

    carry_plan = template.get("carry_plan", {})
    primary = carry_plan.get("primary_carry")
    tank = carry_plan.get("main_tank")
    secondary = carry_plan.get("secondary_carries", []) or []
    utility = carry_plan.get("utility_carry")

    holder_rules = template.get("holder_rules", {}) or {}
    carry_placeholders = holder_rules.get("carry_placeholders", []) or []
    tank_placeholders = holder_rules.get("tank_placeholders", []) or []
    utility_placeholders = holder_rules.get("utility_placeholders", []) or []

    def first_on_board(candidates: List[str]) -> Optional[str]:
        for c in candidates:
            if c in board_ids:
                return c
        return None

    if "tank" in tags:
        if tank in board_ids:
            return tank
        ph = first_on_board(tank_placeholders)
        if ph:
            return ph
        for uid in template.get("units", {}).get("required", []):
            if uid in board_ids:
                return uid
        return board_ids[0] if board_ids else final_holder

    if any(t in tags for t in ["antiheal", "shred", "cc", "cleanse"]):
        if utility in board_ids:
            return utility
        ph = first_on_board(utility_placeholders)
        if ph:
            return ph
        for uid in secondary:
            if uid in board_ids:
                return uid
        if primary in board_ids:
            return primary
        return board_ids[0] if board_ids else final_holder

    if primary in board_ids:
        return primary
    for uid in secondary:
        if uid in board_ids:
            return uid
    ph = first_on_board(carry_placeholders)
    if ph:
        return ph

    return board_ids[0] if board_ids else final_holder


def item_actions(pack: Dict[str, Any], template: Dict[str, Any], gs: Dict[str, Any], craftable_now: List[str]) -> List[Dict[str, Any]]:
    desired = desired_items_index(template)
    board_ids = [u["champion_id"] for u in gs["board"]]

    stability = (gs.get("observations", {}) or {}).get("stability", "unknown")
    stage_i = stage_to_int(gs.get("stage", ""))

    actions: List[Dict[str, Any]] = []
    for item_id in craftable_now:
        if item_id not in desired:
            continue

        meta = desired[item_id]
        final_holder = meta["final_holder"]
        is_core = bool(meta["is_core"])
        idx = int(meta["priority_index"])

        now_holder = choose_now_holder(pack, gs, template, item_id, final_holder)

        if is_core:
            action = "slam_now"
            why = "Core item for this line."
        else:
            if stability == "stable" and stage_i <= 40:
                action = "hold"
                why = "Luxury slot; you’re stable early—hold if you want to wait for higher value slams."
            else:
                action = "slam_now"
                why = "You benefit from immediate power; slam to stabilise."

        transfer = None
        if final_holder not in board_ids:
            transfer = {"final_holder": final_holder, "when": "transfer_when_final_holder_is_fielded"}

        actions.append({
            "action": action,
            "item_id": item_id,
            "now_holder": now_holder,
            "final_holder": final_holder,
            "priority": "core" if is_core else "luxury",
            "slot_index": idx,
            "why": why,
            "transfer_plan": transfer
        })

    actions.sort(key=lambda a: (a["priority"] != "core", a["slot_index"], a["item_id"]))
    return actions


def shop_actions(template: Dict[str, Any], gs: Dict[str, Any]) -> List[Dict[str, Any]]:
    board_ids = [u["champion_id"] for u in gs["board"]]
    bench_ids = [u["champion_id"] for u in gs.get("bench", [])]
    owned = set(board_ids + bench_ids)

    required = template.get("units", {}).get("required", [])
    core = template.get("units", {}).get("core", [])

    actions: List[Dict[str, Any]] = []
    for uid in required:
        if uid not in owned:
            actions.append({"action": "priority_buy", "champion_id": uid, "why": "Required for the line."})
    for uid in core:
        if uid not in owned and uid not in required:
            actions.append({"action": "buy_if_seen", "champion_id": uid, "why": "Core board piece."})

    return actions[:12]


def pivot_warnings(template: Dict[str, Any], gs: Dict[str, Any]) -> List[str]:
    stage_i = stage_to_int(gs.get("stage", ""))
    obs = (gs.get("observations", {}) or {})
    contested = set(obs.get("contested_units", []) or [])

    primary = (template.get("carry_plan", {}) or {}).get("primary_carry")
    warnings: List[str] = []

    if stage_i >= 41 and primary in contested:
        warnings.append(f"{primary} looks contested by 4-1+ — be ready to pivot if copies aren’t coming.")
    return warnings


def score_template(pack: Dict[str, Any], template: Dict[str, Any], gs: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    board_ids = [u["champion_id"] for u in gs["board"]]
    bench_ids = [u["champion_id"] for u in gs.get("bench", [])]
    owned = set(board_ids + bench_ids)

    required = template["units"]["required"]
    core = template["units"]["core"]

    req_hit = sum(1 for u in required if u in owned)
    core_hit = sum(1 for u in core if u in owned)
    unit_score = req_hit * 10 + core_hit * 2

    trait_counts = count_traits(pack, board_ids)
    trait_score = 0
    trait_detail = []
    for t in template.get("core_traits", []):
        tid = t["trait"]
        target = t.get("target")
        have = int(trait_counts.get(tid, 0))
        if target:
            trait_score += min(have, target) * 2
            if have >= target:
                trait_score += 5
        else:
            trait_score += have
        trait_detail.append({"trait": tid, "have": have, "target": target})

    craft_now = craftable_items(pack, gs.get("inventory", []))
    desired = desired_items_index(template)
    craft_score = sum(1 for x in craft_now if x in desired) * 3

    total = unit_score + trait_score + craft_score

    breakdown = {
        "unit_score": unit_score,
        "trait_score": trait_score,
        "craft_score": craft_score,
        "traits": trait_detail,
        "req_hit": req_hit,
        "req_total": len(required),
        "core_hit": core_hit,
        "core_total": len(core),
        "craftable_now": craft_now
    }
    return float(total), breakdown


def eval_pivot_triggers(template: Dict[str, Any], gs: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    raw = template.get("pivot_triggers", []) or []
    triggers: List[Dict[str, Any]] = []
    for trig in raw:
        if isinstance(trig, dict):
            normalized = dict(trig)
            normalized["then"] = normalize_then(trig.get("then"))
            triggers.append(normalized)

    stage_i = stage_to_int(gs.get("stage", ""))
    obs = (gs.get("observations", {}) or {})
    stability = obs.get("stability", "unknown")
    contested = set(obs.get("contested_units", []) or [])

    active: List[Dict[str, Any]] = []
    for trig in triggers:
        by_stage = trig.get("by_stage")
        if isinstance(by_stage, str) and stage_to_int(by_stage) > stage_i:
            continue

        if trig.get("if_stable") is True and stability != "stable":
            continue
        if trig.get("if_unstable") is True and stability == "stable":
            continue

        if trig.get("if_contested") is True:
            who = trig.get("contested_unit") or (template.get("carry_plan", {}) or {}).get("primary_carry")
            if not who or who not in contested:
                continue
        if trig.get("if_uncontested") is True:
            who = trig.get("contested_unit") or (template.get("carry_plan", {}) or {}).get("primary_carry")
            if who and who in contested:
                continue

        if_miss = trig.get("if_miss")
        if isinstance(if_miss, str):
            champ, stars = parse_miss_token(if_miss)
            have_stars = unit_stars(gs, champ)
            if stars is None:
                if have_stars > 0:
                    continue
            else:
                if have_stars >= stars:
                    continue

        active.append(dict(trig))

    return triggers, active


# ---------- Public entrypoint for UI + CLI ----------
def recommend(pack_dir: Path, templates_path: Path, gs: Dict[str, Any], top_n: int = 3) -> Dict[str, Any]:
    pack = load_pack(pack_dir)
    templates = load_templates(templates_path)

    scored: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    for t in templates:
        s, breakdown = score_template(pack, t, gs)
        scored.append((s, t, breakdown))
    scored.sort(key=lambda x: x[0], reverse=True)

    tiers = ["primary", "backup", "greedy"]
    cards = []

    for idx, (s, t, breakdown) in enumerate(scored[:top_n]):
        tier = tiers[idx] if idx < len(tiers) else "option"
        craft_now = breakdown["craftable_now"]

        all_trigs, active_trigs = eval_pivot_triggers(t, gs)

        cards.append({
            "tier": tier,
            "template_id": t["id"],
            "template_name": t["name"],
            "set_patch": gs.get("set_patch"),
            "score": s,
            "shop_actions": shop_actions(t, gs),
            "item_actions": item_actions(pack, t, gs, craft_now),
            "level_plan_hint": next((p for p in t.get("level_plan", []) if p.get("stage") == gs.get("stage")), None),
            "pivot_warnings": pivot_warnings(t, gs),
            "pivot_triggers": all_trigs,
            "active_pivot_triggers": active_trigs,
            "reasons": breakdown
        })

    return {
        "cards": cards,
        "meta": {
            "generated_from": {
                "templates": str(templates_path).replace("\\", "/"),
                "gamestate": "(from_ui_or_cli)"
            }
        }
    }
