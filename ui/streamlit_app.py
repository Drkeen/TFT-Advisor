from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import streamlit as st
from PIL import Image, ImageDraw

# --- Ensure repo root is on PYTHONPATH so `import tft_advisor` works when run via Streamlit
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Recommender imports stay (we'll wire them in once we produce gamestate automatically)
# from tft_advisor.recommender import recommend, load_pack  # later

from streamlit_drawable_canvas import st_canvas

# -------------------------
# Config
# -------------------------
DEFAULT_PACK = Path("data/set16_16.1")
DEFAULT_TEMPLATES = Path("templates/set16_16.1/builds")

CAPTURE_DIR = Path("captures")
CALIB_DIR = Path("ui")
CALIB_PATH = CALIB_DIR / "calibration_2560x1440.json"

TARGET_W = 2560
TARGET_H = 1440


# -------------------------
# Types
# -------------------------
@dataclass
class ROI:
    x: int
    y: int
    w: int
    h: int

    def as_tuple(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def clamp(self, img_w: int, img_h: int) -> "ROI":
        x = max(0, min(self.x, img_w - 1))
        y = max(0, min(self.y, img_h - 1))
        w = max(1, min(self.w, img_w - x))
        h = max(1, min(self.h, img_h - y))
        return ROI(x, y, w, h)


@dataclass
class GridSpec:
    cols: int
    rows: int


# -------------------------
# Helpers: calibration IO
# -------------------------
def load_calibration() -> Optional[Dict[str, Any]]:
    if not CALIB_PATH.exists():
        return None
    try:
        return json.loads(CALIB_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def save_calibration(calib: Dict[str, Any]) -> None:
    CALIB_DIR.mkdir(parents=True, exist_ok=True)
    CALIB_PATH.write_text(json.dumps(calib, indent=2), encoding="utf-8")


def default_calibration_shell() -> Dict[str, Any]:
    return {
        "screen": {"w": TARGET_W, "h": TARGET_H},
        "rois": {
            # You will draw these in calibration:
            "hud": None,        # stage/level/gold/hp area
            "board": None,      # board champions area
            "bench": None,      # bench champions area
            "inventory": None,  # item bench area
        },
        "grids": {
            "board": {"cols": 5, "rows": 2},      # 10 slots
            "bench": {"cols": 3, "rows": 3},      # 9 slots
            "inventory": {"cols": 10, "rows": 1}, # tweak if your layout differs
        },
    }


# -------------------------
# Helpers: capture
# -------------------------
def capture_screen_mss() -> Image.Image:
    """
    Captures the primary monitor using MSS and returns a PIL Image (RGB).
    Note: Some fullscreen modes can return a black image depending on GPU/overlay.
    If that happens, try borderless windowed.
    """
    import mss  # local import

    with mss.mss() as sct:
        mon = sct.monitors[3]  # primary monitor
        img = sct.grab(mon)
        # MSS returns BGRA
        arr = np.array(img)[:, :, :3][:, :, ::-1]  # BGRA->BGR->RGB
        pil = Image.fromarray(arr, mode="RGB")
        return pil


def save_capture(img: Image.Image) -> Path:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CAPTURE_DIR / f"capture_{ts}.png"
    img.save(path)
    return path


def open_image(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


# -------------------------
# Helpers: ROI crop + slots
# -------------------------
def crop_roi(img: Image.Image, roi: ROI) -> Image.Image:
    roi = roi.clamp(img.width, img.height)
    return img.crop((roi.x, roi.y, roi.x + roi.w, roi.y + roi.h))


def draw_roi_overlay(img: Image.Image, rois: Dict[str, Optional[ROI]]) -> Image.Image:
    out = img.copy()
    d = ImageDraw.Draw(out)
    for name, r in rois.items():
        if not r:
            continue
        r = r.clamp(out.width, out.height)
        d.rectangle((r.x, r.y, r.x + r.w, r.y + r.h), outline="red", width=3)
        d.text((r.x + 6, r.y + 6), name, fill="red")
    return out


def split_grid(img: Image.Image, roi: ROI, grid: GridSpec) -> List[Image.Image]:
    """
    Split roi into grid.cols * grid.rows equal cells (simple).
    """
    roi = roi.clamp(img.width, img.height)
    cropped = crop_roi(img, roi)
    cell_w = cropped.width / grid.cols
    cell_h = cropped.height / grid.rows

    cells: List[Image.Image] = []
    for r in range(grid.rows):
        for c in range(grid.cols):
            x0 = int(round(c * cell_w))
            y0 = int(round(r * cell_h))
            x1 = int(round((c + 1) * cell_w))
            y1 = int(round((r + 1) * cell_h))
            cells.append(cropped.crop((x0, y0, x1, y1)))
    return cells


def parse_roi_from_canvas(canvas_json: Dict[str, Any]) -> Optional[ROI]:
    """
    Reads the last rectangle object drawn in streamlit-drawable-canvas.
    """
    if not canvas_json:
        return None
    objs = canvas_json.get("objects") or []
    if not objs:
        return None
    # find last rect-like object
    for obj in reversed(objs):
        if obj.get("type") in ("rect",):
            left = int(obj.get("left", 0))
            top = int(obj.get("top", 0))
            width = int(obj.get("width", 1))
            height = int(obj.get("height", 1))
            return ROI(left, top, width, height)
    return None


# -------------------------
# UI
# -------------------------
def inject_css():
    st.markdown(
        """
        <style>
          html, body, [class*="css"]  { font-size: 13px !important; }
          .block-container { padding-top: 0.8rem; padding-bottom: 1.0rem; }
          div[data-testid="stVerticalBlock"] { gap: 0.5rem; }
          h2, h3 { margin-bottom: 0.4rem; }
          /* tighten widget spacing */
          div[data-testid="stSelectbox"] > div { min-height: 36px; }
          div[data-testid="stSelectbox"] div[role="combobox"] { padding-top: 2px; padding-bottom: 2px; }
          div[data-testid="stNumberInput"] input { padding-top: 2px; padding-bottom: 2px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def page_capture(calib: Dict[str, Any]):
    st.subheader("Capture")

    c1, c2 = st.columns([1.2, 1])
    with c1:
        if st.button("📸 Capture Screen Now", use_container_width=True):
            img = capture_screen_mss()
            path = save_capture(img)
            st.session_state["last_capture_path"] = str(path)
            st.success(f"Captured: {path.as_posix()}")

    with c2:
        st.caption("Tip: If you get a black screenshot in fullscreen, try borderless windowed mode.")

    st.divider()

    # Pick an image source
    last_path = st.session_state.get("last_capture_path")
    files = sorted(CAPTURE_DIR.glob("capture_*.png")) if CAPTURE_DIR.exists() else []
    options = ["(none)"] + [p.name for p in reversed(files[:50])]
    default_idx = 0
    if last_path:
        try:
            name = Path(last_path).name
            if name in options:
                default_idx = options.index(name)
        except Exception:
            pass

    pick = st.selectbox("Preview capture", options, index=default_idx)

    if pick == "(none)":
        st.info("Capture a screenshot to begin.")
        return

    img = open_image(CAPTURE_DIR / pick)

    # Overlay ROIs if calibrated
    rois_raw = (calib.get("rois") or {})
    rois: Dict[str, Optional[ROI]] = {}
    for k, v in rois_raw.items():
        if isinstance(v, dict):
            rois[k] = ROI(int(v["x"]), int(v["y"]), int(v["w"]), int(v["h"]))
        else:
            rois[k] = None

    overlay = draw_roi_overlay(img, rois)
    st.image(overlay, caption="Latest capture (ROI overlay if calibrated)", use_container_width=True)

    # Show cropped regions + grids
    missing = [k for k, v in rois.items() if v is None]
    if missing:
        st.warning(f"Calibration missing ROIs: {', '.join(missing)}. Go to Calibration tab.")
        return

    grids = calib.get("grids") or {}
    board_grid = GridSpec(**grids.get("board", {"cols": 5, "rows": 2}))
    bench_grid = GridSpec(**grids.get("bench", {"cols": 3, "rows": 3}))
    inv_grid = GridSpec(**grids.get("inventory", {"cols": 10, "rows": 1}))

    hud_img = crop_roi(img, rois["hud"])
    board_cells = split_grid(img, rois["board"], board_grid)
    bench_cells = split_grid(img, rois["bench"], bench_grid)
    inv_cells = split_grid(img, rois["inventory"], inv_grid)

    st.divider()
    st.subheader("Extracted regions (what we’ll parse next)")

    with st.expander("HUD (stage/level/gold/hp)"):
        st.image(hud_img, use_container_width=True)

    with st.expander("Board slots"):
        # show as a grid of images
        cols_per_row = board_grid.cols
        for r in range(board_grid.rows):
            row = st.columns(cols_per_row)
            for c in range(cols_per_row):
                idx = r * cols_per_row + c
                row[c].image(board_cells[idx], use_container_width=True)

    with st.expander("Bench slots"):
        cols_per_row = bench_grid.cols
        for r in range(bench_grid.rows):
            row = st.columns(cols_per_row)
            for c in range(cols_per_row):
                idx = r * cols_per_row + c
                row[c].image(bench_cells[idx], use_container_width=True)

    with st.expander("Inventory slots"):
        cols_per_row = inv_grid.cols
        row = st.columns(cols_per_row)
        for i in range(min(len(inv_cells), cols_per_row)):
            row[i].image(inv_cells[i], use_container_width=True)

    st.info("Next step: attach recognition to these slot crops (champ/item icon matching + stars).")


def page_calibration(calib: Dict[str, Any]):
    st.subheader("Calibration (one-time)")
    st.write("Draw rectangles for: **hud**, **board**, **bench**, **inventory**. Then set grid sizes and Save.")

    # Choose a base image: latest capture or upload
    st.divider()
    left, right = st.columns([1, 1])

    with left:
        files = sorted(CAPTURE_DIR.glob("capture_*.png")) if CAPTURE_DIR.exists() else []
        if not files:
            st.warning("No captures found yet. Go to Capture tab and grab one first.")
            return
        pick = st.selectbox("Use captured image", [p.name for p in reversed(files[:50])], index=0)
        img = open_image(CAPTURE_DIR / pick)

    with right:
        st.caption("Optional: upload a screenshot instead")
        up = st.file_uploader("Upload PNG/JPG", type=["png", "jpg", "jpeg"])
        if up:
            img = Image.open(up).convert("RGB")

    st.divider()

    # Ensure we calibrate for the active image’s size
    st.caption(f"Image size: {img.width}×{img.height} (expected {TARGET_W}×{TARGET_H})")
    if img.width != TARGET_W or img.height != TARGET_H:
        st.warning("Your screenshot resolution differs from 2560×1440. Calibration will be resolution-specific.")

    # Choose which ROI we're drawing now
    roi_name = st.selectbox("ROI to draw", ["hud", "board", "bench", "inventory"])

    # Existing roi to show
    rois_raw = calib.get("rois") or {}
    existing = rois_raw.get(roi_name)
    existing_roi = None
    if isinstance(existing, dict):
        existing_roi = ROI(int(existing["x"]), int(existing["y"]), int(existing["w"]), int(existing["h"]))

    # Canvas
    st.write("Draw a rectangle on the image (click-drag).")
    canvas_result = st_canvas(
        fill_color="rgba(255, 0, 0, 0.1)",
        stroke_width=3,
        stroke_color="rgba(255,0,0,0.9)",
        background_image=img,
        update_streamlit=True,
        height=int(img.height * 0.55),  # keeps UI manageable
        width=int(img.width * 0.55),
        drawing_mode="rect",
        key=f"canvas_{roi_name}",
    )

    # Canvas is scaled, so we need to map back to full image coords
    scale_x = img.width / (int(img.width * 0.55))
    scale_y = img.height / (int(img.height * 0.55))

    drawn = parse_roi_from_canvas(canvas_result.json_data or {})
    if drawn:
        roi_full = ROI(
            x=int(drawn.x * scale_x),
            y=int(drawn.y * scale_y),
            w=int(drawn.w * scale_x),
            h=int(drawn.h * scale_y),
        )
        st.success(f"Drawn ROI for {roi_name}: {roi_full.as_tuple()}")

        if st.button(f"Save ROI: {roi_name}", use_container_width=True):
            calib.setdefault("rois", {})
            calib["rois"][roi_name] = {"x": roi_full.x, "y": roi_full.y, "w": roi_full.w, "h": roi_full.h}
            save_calibration(calib)
            st.success("Saved calibration.")

    # Grid specs
    st.divider()
    st.subheader("Grid sizes")

    grids = calib.get("grids") or {}
    b = grids.get("board", {"cols": 5, "rows": 2})
    bn = grids.get("bench", {"cols": 3, "rows": 3})
    inv = grids.get("inventory", {"cols": 10, "rows": 1})

    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown("**Board**")
        bc = st.number_input("Cols", 1, 10, int(b.get("cols", 5)), key="board_cols")
        br = st.number_input("Rows", 1, 10, int(b.get("rows", 2)), key="board_rows")
    with g2:
        st.markdown("**Bench**")
        bnc = st.number_input("Cols ", 1, 10, int(bn.get("cols", 3)), key="bench_cols")
        bnr = st.number_input("Rows ", 1, 10, int(bn.get("rows", 3)), key="bench_rows")
    with g3:
        st.markdown("**Inventory**")
        ic = st.number_input("Cols  ", 1, 20, int(inv.get("cols", 10)), key="inv_cols")
        ir = st.number_input("Rows  ", 1, 5, int(inv.get("rows", 1)), key="inv_rows")

    if st.button("Save grid sizes", use_container_width=True):
        calib.setdefault("grids", {})
        calib["grids"]["board"] = {"cols": int(bc), "rows": int(br)}
        calib["grids"]["bench"] = {"cols": int(bnc), "rows": int(bnr)}
        calib["grids"]["inventory"] = {"cols": int(ic), "rows": int(ir)}
        save_calibration(calib)
        st.success("Saved grid sizes.")


def page_status(calib: Dict[str, Any]):
    st.subheader("Status")
    st.write("This page shows what the app currently knows and where files live.")

    st.markdown("**Paths**")
    st.code(
        "\n".join(
            [
                f"Repo root: {REPO_ROOT}",
                f"Capture dir: {CAPTURE_DIR.resolve()}",
                f"Calibration file: {CALIB_PATH.resolve()}",
                f"Default pack: {DEFAULT_PACK}",
                f"Default templates: {DEFAULT_TEMPLATES}",
            ]
        )
    )

    st.markdown("**Calibration JSON**")
    st.json(calib)

    st.markdown("**Captures**")
    files = sorted(CAPTURE_DIR.glob("capture_*.png")) if CAPTURE_DIR.exists() else []
    st.write(f"{len(files)} capture(s) found.")
    if files:
        st.write("Latest:", files[-1].name)


def main():
    st.set_page_config(page_title="TFT Advisor (Capture v2)", layout="wide")
    inject_css()

    st.title("TFT Advisor — Capture v2")

    # Load or initialize calibration
    calib = load_calibration()
    if not calib:
        calib = default_calibration_shell()
        # don't auto-save until user calibrates

    tabs = st.tabs(["📸 Capture", "🧭 Calibration", "ℹ️ Status"])

    with tabs[0]:
        page_capture(calib)

    with tabs[1]:
        page_calibration(calib)

    with tabs[2]:
        page_status(calib)


if __name__ == "__main__":
    main()
