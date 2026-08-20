#!/usr/bin/env python3
"""
Render static/architecture.png, served by GET /api/model_architecture.

    python scripts/render_architecture.py

The module names drawn here are a contract. They must match the `module` field
in the steps trace and the names used in /api/agent_info. Rename a box here and
you must rename it in both other places, or the brief's consistency requirement
is broken.

Drawn at 2x and downsampled, because Pillow has no antialiasing on shapes.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "architecture.png"

sys.path.insert(0, str(ROOT))
from app.tools.registry import TOOLS  # noqa: E402

# Counted, not typed. This box said "10 deterministic tools" and went on saying
# it while five more tools were added. A diagram that quietly disagrees with
# the system is worse than a diagram that omits the number.
TOOL_COUNT = len(TOOLS)

S = 2
W, H = 1280 * S, 860 * S

INK = (24, 26, 32)
MUTED = (110, 118, 132)
LINE = (150, 158, 172)
PAGE = (255, 255, 255)

AGENT_FILL = (232, 240, 254)
AGENT_EDGE = (66, 118, 210)
TOOL_FILL = (233, 246, 236)
TOOL_EDGE = (52, 140, 82)
SERVICE_FILL = (253, 240, 230)
SERVICE_EDGE = (198, 118, 48)
IO_FILL = (242, 243, 246)
IO_EDGE = (140, 148, 162)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
BOLD_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size, bold=False):
    for path in (BOLD_CANDIDATES if bold else FONT_CANDIDATES):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size * S)
            except OSError:
                continue
    return ImageFont.load_default()


F_TITLE = font(30, bold=True)
F_SUB = font(15)
F_BOX = font(17, bold=True)
F_SMALL = font(13)
F_TINY = font(12)
F_LABEL = font(12)


def box(d, xy, title, subtitle=None, fill=IO_FILL, edge=IO_EDGE, radius=10):
    x0, y0, x1, y1 = [v * S for v in xy]
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius * S, fill=fill,
                        outline=edge, width=2 * S)
    cx = (x0 + x1) / 2
    if subtitle:
        d.text((cx, (y0 + y1) / 2 - 10 * S), title, font=F_BOX, fill=INK, anchor="mm")
        d.text((cx, (y0 + y1) / 2 + 11 * S), subtitle, font=F_SMALL, fill=MUTED, anchor="mm")
    else:
        d.text((cx, (y0 + y1) / 2), title, font=F_BOX, fill=INK, anchor="mm")


def arrow(d, start, end, label=None, colour=LINE, dashed=False, label_side="above"):
    x0, y0 = [v * S for v in start]
    x1, y1 = [v * S for v in end]
    if dashed:
        _dashed_line(d, (x0, y0), (x1, y1), colour)
    else:
        d.line([x0, y0, x1, y1], fill=colour, width=2 * S)
    _head(d, (x0, y0), (x1, y1), colour)
    if label:
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        offset = -11 * S if label_side == "above" else 13 * S
        d.text((mx, my + offset), label, font=F_LABEL, fill=MUTED, anchor="mm")


def _dashed_line(d, a, b, colour, dash=9, gap=7):
    import math
    (x0, y0), (x1, y1) = a, b
    total = math.hypot(x1 - x0, y1 - y0)
    if total == 0:
        return
    ux, uy = (x1 - x0) / total, (y1 - y0) / total
    pos = 0.0
    while pos < total:
        seg = min(dash * S, total - pos)
        d.line([x0 + ux * pos, y0 + uy * pos,
                x0 + ux * (pos + seg), y0 + uy * (pos + seg)],
               fill=colour, width=2 * S)
        pos += seg + gap * S


def _head(d, a, b, colour, size=9):
    import math
    (x0, y0), (x1, y1) = a, b
    angle = math.atan2(y1 - y0, x1 - x0)
    s = size * S
    d.polygon([
        (x1, y1),
        (x1 - s * math.cos(angle - 0.42), y1 - s * math.sin(angle - 0.42)),
        (x1 - s * math.cos(angle + 0.42), y1 - s * math.sin(angle + 0.42)),
    ], fill=colour)


def legend(d, x, y, items):
    for i, (colour, fill, text) in enumerate(items):
        yy = y + i * 26
        d.rounded_rectangle([x * S, yy * S, (x + 26) * S, (yy + 17) * S],
                            radius=4 * S, fill=fill, outline=colour, width=2 * S)
        d.text(((x + 36) * S, (yy + 8) * S), text, font=F_SMALL, fill=MUTED, anchor="lm")


def main():
    img = Image.new("RGB", (W, H), PAGE)
    d = ImageDraw.Draw(img)

    d.text((60 * S, 40 * S), "BarMate", font=F_TITLE, fill=INK, anchor="lt")
    d.text((60 * S, 78 * S),
           "Single agent. ReAct loop with a Reflect gate. No intent router, no sub-agents.",
           font=F_SUB, fill=MUTED, anchor="lt")

    # Request path across the top.
    box(d, (60, 130, 250, 190), "Manager", "GUI at /", fill=IO_FILL, edge=IO_EDGE)
    box(d, (330, 130, 560, 190), "POST /api/execute", "Flask, Vercel",
        fill=IO_FILL, edge=IO_EDGE)

    # The loop.
    d.rounded_rectangle([330 * S, 240 * S, 900 * S, 470 * S], radius=14 * S,
                        outline=(190, 205, 235), width=2 * S)
    d.text((350 * S, 258 * S), "ReAct loop, max 8 iterations",
           font=F_TINY, fill=MUTED, anchor="lt")

    box(d, (360, 290, 600, 360), "Reasoner", "chooses the next action",
        fill=AGENT_FILL, edge=AGENT_EDGE)
    box(d, (660, 290, 870, 360), "Tool registry",
        f"{TOOL_COUNT} deterministic tools",
        fill=TOOL_FILL, edge=TOOL_EDGE)
    box(d, (360, 390, 600, 450), "KnowledgeRetriever", "embeds query, ranks docs",
        fill=AGENT_FILL, edge=AGENT_EDGE)

    # The gate.
    box(d, (330, 540, 560, 610), "Reflector", "answer good enough?",
        fill=AGENT_FILL, edge=AGENT_EDGE)
    box(d, (660, 540, 890, 610), "Reviser", "repairs the answer",
        fill=AGENT_FILL, edge=AGENT_EDGE)
    box(d, (60, 540, 250, 610), "Response + steps", "JSON", fill=IO_FILL, edge=IO_EDGE)

    # External services.
    box(d, (990, 290, 1220, 360), "Supabase", "venue ledger, Postgres",
        fill=SERVICE_FILL, edge=SERVICE_EDGE)
    box(d, (990, 390, 1220, 450), "Pinecone", "14 docs, 1536-dim",
        fill=SERVICE_FILL, edge=SERVICE_EDGE)
    box(d, (990, 130, 1220, 190), "LLMod.ai", "gpt-5.4-mini",
        fill=SERVICE_FILL, edge=SERVICE_EDGE)

    # Wiring.
    arrow(d, (250, 160), (330, 160), "prompt")
    arrow(d, (445, 190), (445, 290), "")
    arrow(d, (600, 310), (660, 310), "call")
    arrow(d, (660, 345), (600, 345), "observation", label_side="below")
    arrow(d, (480, 360), (480, 390), "")
    arrow(d, (870, 325), (990, 325), "SQL over REST")
    arrow(d, (600, 420), (990, 420), "vector query")
    arrow(d, (445, 470), (445, 540), "draft")
    arrow(d, (560, 565), (660, 565), "revise")
    arrow(d, (330, 575), (250, 575), "accept")

    # The revised answer leaves from the Reviser, routed under the row so it
    # does not cross the draft path.
    d.line([775 * S, 610 * S, 775 * S, 640 * S], fill=LINE, width=2 * S)
    d.line([775 * S, 640 * S, 155 * S, 640 * S], fill=LINE, width=2 * S)
    arrow(d, (155, 640), (155, 610), "")
    d.text((465 * S, 628 * S), "revised answer", font=F_LABEL, fill=MUTED, anchor="mm")

    # The LLM is called by three modules, drawn dashed to keep the data path clear.
    for y in (300, 400, 550):
        arrow(d, (1105, y if y != 550 else 560), (1105, 190), colour=(214, 190, 168),
              dashed=True)

    d.text((1105 * S, 218 * S), "every LLM call is traced",
           font=F_TINY, fill=MUTED, anchor="mm")

    legend(d, 60, 690, [
        (AGENT_EDGE, AGENT_FILL, "Agent modules, LLM-backed and traced in steps"),
        (TOOL_EDGE, TOOL_FILL, "Deterministic tools. All arithmetic lives here"),
        (SERVICE_EDGE, SERVICE_FILL, "External services"),
    ])

    d.text((60 * S, 786 * S),
           "The Reasoner decides which tools to call and when it has enough. "
           "There is no up-front intent classifier:",
           font=F_SMALL, fill=MUTED, anchor="lt")
    d.text((60 * S, 808 * S),
           "refusal, clarification and planning are all decisions taken inside the loop. "
           "The model never does arithmetic.",
           font=F_SMALL, fill=MUTED, anchor="lt")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.resize((W // S, H // S), Image.LANCZOS).save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
