from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, ValidationError

from openai import OpenAI


# --------- Structured output models (what we want back) ---------

class UnitOnBoard(BaseModel):
    champion_id: str
    stars: int = Field(default=1, ge=1, le=3)
    items: List[str] = Field(default_factory=list)


class UnitOnBench(BaseModel):
    champion_id: str
    stars: int = Field(default=1, ge=1, le=3)


class Observations(BaseModel):
    stability: Literal["stable", "unstable", "unknown"] = "unknown"
    contested_units: List[str] = Field(default_factory=list)


class GameStateFromVision(BaseModel):
    # Keep defaults so you still get a usable object if the model is uncertain
    set_patch: str = "set16_16.1"
    stage: str = "4-1"
    level: int = 7
    gold: int = 0
    hp: int = 100

    board: List[UnitOnBoard] = Field(default_factory=list)
    bench: List[UnitOnBench] = Field(default_factory=list)
    inventory: List[str] = Field(default_factory=list)
    augments: List[str] = Field(default_factory=list)

    observations: Observations = Field(default_factory=Observations)


# --------- Vision read ---------

def build_vision_prompt(
    set_patch: str,
    champion_ids: List[str],
    item_ids: List[str],
) -> str:
    """
    IMPORTANT: keep this prompt compact-ish. We give the model the allowed IDs so it can
    output normalized identifiers directly.
    """
    # Limiting the lists can help latency/cost if needed later.
    champs_preview = ", ".join(champion_ids)
    items_preview = ", ".join(item_ids)

    return f"""
You are reading a Teamfight Tactics (TFT) screenshot.

Goal:
Extract the current game state into the provided JSON schema.

Rules:
- Output ONLY valid structured data (no extra commentary).
- Use champion_id and item_id EXACTLY as provided in the allowed lists.
- If you are unsure of an ID, omit that unit/item rather than guessing.
- Stars are 1/2/3 only.
- board units include items placed on them (0-3 items).
- bench units do not need items.
- inventory includes BOTH components and completed items visible in the inventory bar.
- augments: include augment IDs/names if clearly visible; otherwise return [].
- observations.stability: choose "stable" / "unstable" / "unknown". If unclear, "unknown".
- observations.contested_units: leave [] for now unless it is explicitly provided (usually it won't be visible).

Set patch to use: {set_patch}

Allowed champion_ids:
{champs_preview}

Allowed item_ids:
{items_preview}
""".strip()


def read_gamestate_from_screenshot(
    *,
    api_key: str,
    model: str,
    set_patch: str,
    screenshot_data_url: str,
    champion_ids: List[str],
    item_ids: List[str],
) -> Dict[str, Any]:
    """
    Calls OpenAI vision + structured parsing and returns a plain dict suitable for recommend().
    """
    client = OpenAI(api_key=api_key)

    prompt = build_vision_prompt(set_patch=set_patch, champion_ids=champion_ids, item_ids=item_ids)

    # Images as input via Responses API (data URL) :contentReference[oaicite:3]{index=3}
    # Structured parse via responses.parse + Pydantic :contentReference[oaicite:4]{index=4}
    resp = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": "You extract structured TFT game-state data from screenshots."},
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": screenshot_data_url},
                ],
            },
        ],
        text_format=GameStateFromVision,
    )

    gs: GameStateFromVision = resp.output_parsed  # type: ignore
    return gs.model_dump()
