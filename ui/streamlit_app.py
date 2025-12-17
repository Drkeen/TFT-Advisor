from __future__ import annotations

import sys
from pathlib import Path
import os

import streamlit as st

# Ensure repo root is on PYTHONPATH so `import tft_advisor` works when run via Streamlit
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tft_advisor.recommender import load_pack, recommend
from tft_advisor.vision_capture import capture_monitor_png
from tft_advisor.vision_reader import read_gamestate_from_screenshot


DEFAULT_PACK = Path("data/set16_16.1")
DEFAULT_TEMPLATES = Path("templates/set16_16.1/builds")


def _small_css():
    st.markdown(
        """
<style>
/* Slightly smaller fonts + tighter spacing */
html, body, [class*="css"]  { font-size: 13px !important; }
.block-container { padding-top: 1.2rem; padding-bottom: 1.2rem; }
h1 { font-size: 1.6rem !important; }
h2 { font-size: 1.2rem !important; }
h3 { font-size: 1.05rem !important; }

/* Make sidebar less chunky */
section[data-testid="stSidebar"] { width: 280px !important; }
section[data-testid="stSidebar"] > div { padding-top: 0.75rem; }
</style>
""",
        unsafe_allow_html=True,
    )


@st.cache_data
def load_pack_cached(pack_dir_str: str):
    return load_pack(Path(pack_dir_str))


def main():
    st.set_page_config(page_title="TFT Advisor", layout="wide")
    _small_css()

    st.title("TFT Advisor — Screenshot → GameState → Recommendations")

    with st.sidebar:
        st.header("Setup")
        pack_dir = st.text_input("Pack folder", str(DEFAULT_PACK))
        templates_dir = st.text_input("Templates folder", str(DEFAULT_TEMPLATES))

        st.divider()
        st.header("Capture")
        monitor_index = st.number_input("Monitor index (TFT fullscreen)", min_value=1, max_value=8, value=3, step=1)

        st.divider()
        st.header("AI (OpenAI)")
        api_key = st.text_input(
            "OPENAI_API_KEY",
            value=os.environ.get("OPENAI_API_KEY", ""),
            type="password",
            help="Do not commit this key to git.",
        )
        model = st.text_input(
            "Vision model",
            value="gpt-4o-2024-08-06",
            help="This must be a vision-capable model that supports structured outputs.",
        )

    # Load pack (for allowed IDs + recommend)
    pack = load_pack_cached(pack_dir)
    champ_ids = sorted(pack["champions"].keys())
    item_ids = sorted(pack["items"].keys())
    set_patch = Path(pack_dir).name

    # Layout: left = screenshot + parsed JSON, right = recommendations
    left, right = st.columns([1.05, 1.0], gap="large")

    with left:
        st.subheader("1) Capture screenshot")
        cap_cols = st.columns([1, 1, 2])
        if cap_cols[0].button("Capture now", use_container_width=True):
            try:
                cap = capture_monitor_png(int(monitor_index))
                st.session_state["last_capture"] = cap
                st.success(f"Captured monitor {cap.monitor_index} ({cap.size[0]}x{cap.size[1]})")
            except Exception as e:
                st.error(f"Capture failed: {e}")

        if cap_cols[1].button("Clear capture", use_container_width=True):
            st.session_state.pop("last_capture", None)
            st.session_state.pop("last_gamestate", None)
            st.session_state.pop("last_recommendation", None)

        cap = st.session_state.get("last_capture")
        if cap:
            st.image(cap.png_bytes, caption=f"Monitor {cap.monitor_index} • {cap.size[0]}x{cap.size[1]}", use_container_width=True)
        else:
            st.info("Capture a screenshot from your TFT monitor.")

        st.divider()
        st.subheader("2) Read screenshot → GameState JSON")

        read_cols = st.columns([1, 1, 1])
        can_read = bool(cap) and bool(api_key.strip())

        if read_cols[0].button("Read with AI", use_container_width=True, disabled=not can_read):
            try:
                gs = read_gamestate_from_screenshot(
                    api_key=api_key.strip(),
                    model=model.strip(),
                    set_patch=set_patch,
                    screenshot_data_url=cap.data_url,
                    champion_ids=champ_ids,
                    item_ids=item_ids,
                )
                st.session_state["last_gamestate"] = gs
                st.success("Parsed GameState from screenshot.")
            except Exception as e:
                st.error(f"AI read failed: {e}")

        if read_cols[1].button("Recommend", use_container_width=True, disabled=("last_gamestate" not in st.session_state)):
            try:
                gs = st.session_state["last_gamestate"]
                out = recommend(Path(pack_dir), Path(templates_dir), gs)
                st.session_state["last_recommendation"] = out
                st.success("Generated recommendations.")
            except Exception as e:
                st.error(f"Recommend failed: {e}")

        if read_cols[2].button("Clear GameState", use_container_width=True):
            st.session_state.pop("last_gamestate", None)
            st.session_state.pop("last_recommendation", None)

        gs = st.session_state.get("last_gamestate")
        if gs:
            st.markdown("**GameState (from AI)**")
            st.json(gs)
        else:
            st.caption("After reading, you’ll see the extracted GameState JSON here.")

    with right:
        st.subheader("3) Recommendations")
        out = st.session_state.get("last_recommendation")
        if not out:
            st.info("Once you have a GameState, click **Recommend**.")
        else:
            cards = out.get("cards", [])
            for card in cards:
                with st.container(border=True):
                    tier = (card.get("tier") or "").upper()
                    st.markdown(f"### {tier}: {card.get('template_name')} — Score {card.get('score')}")

                    # Key outputs
                    if card.get("pivot_warnings"):
                        for w in card["pivot_warnings"]:
                            st.warning(w)

                    active = card.get("active_pivot_triggers", []) or []
                    if active:
                        st.markdown("**Active pivot triggers**")
                        for t in active:
                            then = t.get("then") or {}
                            msg = then.get("why") or then.get("message") or str(then)
                            st.info(msg)

                    st.markdown("**Shop actions**")
                    for a in card.get("shop_actions", []) or []:
                        st.write(f"- **{a.get('action')}** `{a.get('champion_id')}` — {a.get('why','')}")

                    st.markdown("**Item actions**")
                    for a in card.get("item_actions", []) or []:
                        line = f"- **{a.get('action')}** `{a.get('item_id')}` on `{a.get('now_holder')}`"
                        if a.get("final_holder") and a["final_holder"] != a["now_holder"]:
                            line += f" → final `{a.get('final_holder')}`"
                        st.write(line)

                    lp = card.get("level_plan_hint")
                    if lp:
                        st.markdown(f"**Level plan hint:** Stage `{lp.get('stage')}`, Level `{lp.get('level')}` — `{lp.get('rule')}`")

                    with st.expander("Why this scored well"):
                        st.json(card.get("reasons", {}))


if __name__ == "__main__":
    main()
