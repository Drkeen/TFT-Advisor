from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import base64
import io

import mss
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class CaptureResult:
    monitor_index: int
    size: Tuple[int, int]
    png_bytes: bytes
    data_url: str  # data:image/png;base64,...


def capture_monitor_png(monitor_index: int = 1) -> CaptureResult:
    """
    Captures an entire monitor using MSS and returns PNG bytes + a data URL.

    monitor_index: 1..N where sct.monitors[0] is the "all monitors" virtual screen.
    """
    with mss.mss() as sct:
        if monitor_index < 1 or monitor_index >= len(sct.monitors):
            raise ValueError(
                f"Invalid monitor_index={monitor_index}. Available: 1..{len(sct.monitors)-1}"
            )

        mon = sct.monitors[monitor_index]
        grab = sct.grab(mon)

        # MSS returns BGRA; convert to RGB via numpy
        arr = np.array(grab)  # shape (h, w, 4)
        rgb = arr[:, :, :3][:, :, ::-1]  # BGRA -> RGB

        img = Image.fromarray(rgb, mode="RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()

    b64 = base64.b64encode(png_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{b64}"
    return CaptureResult(
        monitor_index=monitor_index,
        size=img.size,
        png_bytes=png_bytes,
        data_url=data_url,
    )
