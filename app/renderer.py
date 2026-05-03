"""
E-paper PNG renderer for Kindle 600×800 landscape (800×600px output).

Draws the portfolio layout using Pillow:
  - Left panel : total value + 3 metric tiles
  - Right panel: monthly dividend bar chart with rotated labels

Palette is strict 4-level greyscale to look crisp on e-ink:
  WHITE  #F0EDE6  (paper)
  LIGHT  #C8C4BB  (grid lines, tile borders)
  MID    #666660  (secondary text)
  BLACK  #1A1A1A  (primary text, bars)
"""

from __future__ import annotations

import io
import os
import math
from datetime import datetime, timezone
from calendar import month_abbr
from PIL import Image, ImageDraw, ImageFont

from .models import PortfolioSummary

# ── Canvas ──────────────────────────────────────────────────────────────────
W, H = 800, 600
PAD  = 22          # outer margin
GAP  = 14          # gap between left panel and chart

# ── Palette ─────────────────────────────────────────────────────────────────
WHITE = "#F0EDE6"
LIGHT = "#C8C4BB"
MID   = "#888880"
BLACK = "#1A1A1A"

# ── Font paths (bundled in Docker image) ────────────────────────────────────
_FONT_DIR  = os.environ.get("FONT_DIR", "/usr/share/fonts/truetype/dejavu")
_SANS      = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_SANS_BOLD = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")
_MONO      = os.path.join(_FONT_DIR, "DejaVuSansMono.ttf")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _text_h(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[3] - bbox[1]


def render_png(summary: PortfolioSummary, monthly_divs: list[tuple[int, int, float]] | None = None) -> bytes:
    img  = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # ── Fonts ────────────────────────────────────────────────────────────────
    f_hero     = _font(_SANS_BOLD, 34)
    f_label    = _font(_SANS,      13)
    f_label_sm = _font(_SANS,      11)
    f_tile_val = _font(_SANS_BOLD, 18)
    f_tile_lbl = _font(_SANS,      11)
    f_header   = _font(_MONO,      12)
    f_bar_lbl  = _font(_MONO,      11)
    f_month    = _font(_MONO,      11)
    f_footer   = _font(_MONO,      10)

    # ── Layout geometry ──────────────────────────────────────────────────────
    left_w    = 170          # width of left summary panel
    divider_x = PAD + left_w + GAP // 2
    chart_x   = divider_x + GAP // 2 + 2
    chart_w   = W - chart_x - PAD - 30   # 30px reserved for y-axis labels on right
    header_h  = 28           # height of top header bar
    footer_h  = 18
    body_top  = PAD + header_h + 6
    body_bot  = H - PAD - footer_h - 6

    # ════════════════════════════════════════════════════════════════════════
    # HEADER
    # ════════════════════════════════════════════════════════════════════════
    now_str = datetime.now(timezone.utc).strftime("%a %d %b %Y · %H:%M UTC")
    market_str = "MARKET OPEN" if summary.market_open else "MARKET CLOSED"

    draw.text((PAD, PAD + 4), "PORTFOLIO", font=f_header, fill=MID)
    mw = _text_w(draw, market_str, f_header)
    draw.text((W - PAD - mw, PAD + 4), market_str, font=f_header, fill=BLACK)
    nw = _text_w(draw, now_str, f_label_sm)
    draw.text(((W - nw) // 2, PAD + 5), now_str, font=f_label_sm, fill=MID)

    # Header underline
    draw.line([(PAD, PAD + header_h), (W - PAD, PAD + header_h)], fill=BLACK, width=2)

    # ════════════════════════════════════════════════════════════════════════
    # LEFT PANEL — total value + tiles
    # ════════════════════════════════════════════════════════════════════════
    panel_cx = PAD + left_w // 2

    # Hero value
    hero_str = f"£{summary.total_current_value:,.0f}"
    hw = _text_w(draw, hero_str, f_hero)
    hero_y = body_top + 10
    draw.text(((PAD + left_w - hw) // 2 + PAD // 2, hero_y+15), hero_str, font=f_hero, fill=BLACK)

    lbl = "total value"
    lw = _text_w(draw, lbl, f_label)
    draw.text((panel_cx - lw // 2, hero_y + 58), lbl, font=f_label, fill=MID)

    # Thin rule under hero
    rule_y = hero_y + 78
    draw.line([(PAD, rule_y), (PAD + left_w, rule_y)], fill=LIGHT, width=1)

    # Four metric tiles
    tiles = [
        ("cash",         f"£{summary.cash_balance:,.0f}"),
        ("P&L (trades)", _fmt_pct(summary.total_gain_loss_pct)),
        ("P&L (cash)",   _fmt_pct(summary.cash_on_cash_pct)),
        ("dividends",    f"£{summary.total_dividends:,.0f}"),
    ]
    tile_top   = rule_y + 8
    tile_h     = 34
    tile_gap   = 6
    tile_left  = PAD
    tile_right = PAD + left_w

    for i, (lbl, val) in enumerate(tiles):
        ty = tile_top + i * (tile_h + tile_gap)
        draw.rounded_rectangle(
            [(tile_left, ty), (tile_right, ty + tile_h)],
            radius=4, outline=LIGHT, width=1
        )
        vw = _text_w(draw, val, f_tile_val)
        draw.text((panel_cx - vw // 2, ty + 4), val, font=f_tile_val, fill=BLACK)
        lw2 = _text_w(draw, lbl, f_tile_lbl)
        draw.text((panel_cx - lw2 // 2, ty + 20), lbl, font=f_tile_lbl, fill=MID)

    # ════════════════════════════════════════════════════════════════════════
    # VERTICAL DIVIDER
    # ════════════════════════════════════════════════════════════════════════
    draw.line([(divider_x, body_top), (divider_x, body_bot)], fill=LIGHT, width=1)

    # ════════════════════════════════════════════════════════════════════════
    # RIGHT PANEL — dividend bar chart
    # ════════════════════════════════════════════════════════════════════════
    # chart_label = "DIVIDENDS · LAST 12 MONTHS"
    # draw.text((chart_x, body_top + 2), chart_label, font=f_header, fill=MID)

    # Chart area bounds
    chart_top    = body_top + 22
    chart_bot    = body_bot - 22    # leave room for month labels
    month_lbl_y  = chart_bot + 5
    # chart_height = chart_bot - chart_top

    # Build monthly dividend data
    monthly = _monthly_dividends(monthly_divs or [])

    max_val = max((v for _, v, _ in monthly), default=1.0)
    max_val = max(max_val, 1.0) * 1.15

    n_bars  = len(monthly)
    bar_gap = 4
    bar_w   = max(10, (chart_w - (n_bars - 1) * bar_gap) // n_bars)
    total_bars_w = n_bars * bar_w + (n_bars - 1) * bar_gap
    bar_start_x  = chart_x + (chart_w - total_bars_w) // 2

    # Y-axis grid lines + labels
    y_steps = _nice_steps(max_val, 8)
    print(f'y_steps: {y_steps}') #del
    for step_val in y_steps:
        y_px = _val_to_y(step_val, max_val, chart_top, chart_bot)
        if y_px > chart_top:
            draw.line([(chart_x, y_px), (chart_x + chart_w, y_px)], fill=LIGHT, width=1)
            lbl = f"£{step_val:,.0f}"
            draw.text((chart_x + chart_w + 3, y_px - 6), lbl, font=f_bar_lbl, fill=MID)

    # Bars
    for i, (mon, val, is_current) in enumerate(monthly):
        bx = bar_start_x + i * (bar_w + bar_gap)
        by = _val_to_y(val, max_val, chart_top, chart_bot)

        if is_current:
            draw.rectangle([(bx, by), (bx + bar_w, chart_bot)], fill=LIGHT)
        else:
            draw.rectangle([(bx, by), (bx + bar_w, chart_bot)], fill=BLACK)

        # Amount label — rotated 45° above bar
        if val > 0:
            amt_str = f"£{val:,.0f}"
            _draw_rotated_text(img, amt_str, f_bar_lbl, BLACK,
                               bx + bar_w // 2, by - 4)

        # Month label below baseline
        mw2 = _text_w(draw, mon, f_month)
        draw.text((bx + (bar_w - mw2) // 2, month_lbl_y), mon, font=f_month, fill=MID)

    # Baseline
    draw.line([(bar_start_x, chart_bot), (bar_start_x + total_bars_w, chart_bot)],
              fill=BLACK, width=2)

    # Legend
    legend_y = body_bot + 4
    draw.rectangle([(chart_x + chart_w - 87, legend_y + 7), (chart_x + chart_w - 78, legend_y + 16)], fill=BLACK)
    draw.text((chart_x + chart_w - 75, legend_y+5), "paid", font=f_footer, fill=MID)
    
    draw.rectangle([(chart_x + chart_w - 95 + 48, legend_y + 7), (chart_x + chart_w - 95 + 57, legend_y + 16)], fill=LIGHT)
    draw.text(( chart_x + chart_w - 35 , legend_y+5), "in progress", font=f_footer, fill=MID)

    # ════════════════════════════════════════════════════════════════════════
    # FOOTER
    # ════════════════════════════════════════════════════════════════════════
    draw.line([(PAD, H - PAD - footer_h), (W - PAD, H - PAD - footer_h)],
              fill=LIGHT, width=1)
    as_of_str = f"prices as of last transaction · {summary.as_of.strftime('%d %b %Y %H:%M')}"
    draw.text((PAD, H - PAD - footer_h + 4), as_of_str, font=f_footer, fill=MID)

    # Convert to greyscale then back to RGB (keeps JPEG-safe format)
    img = img.convert("L").convert("RGB")
    img = img.rotate(90, expand=True)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _monthly_dividends(raw: list[tuple[int, int, float]]) -> list[tuple[str, float, bool]]:
    # raw = [(year, month, amount), ...]

    now = datetime.now(timezone.utc)
    result = []

    for offset in range(11, -1, -1):
        # Walk back month by month
        m = now.month - offset
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        val = next((amt for yr, mo, amt in raw if yr == y and mo == m), 0.0)
        is_current = (y == now.year and m == now.month)
        result.append((month_abbr[m], val, is_current))

    return result


def _val_to_y(val: float, max_val: float, top: int, bot: int) -> int:
    available = bot - top
    return bot - int(val / max_val * available)


def _nice_steps(max_val: float, n: int) -> list[float]:
    raw_step = max_val / n
    magnitude = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
    nice = math.ceil(raw_step / magnitude) * magnitude
    return [nice * i for i in range(1, n + 1) if nice * i <= max_val * 1.15]


def _hatch(draw: ImageDraw.ImageDraw, x1: int, y1: int, x2: int, y2: int, color: str,
           spacing: int = 6):
    w = x2 - x1
    h = y2 - y1
    for k in range(-(h), w + h, spacing):
        ax = x1 + max(k, 0)
        ay = y1 + max(-k, 0)
        bx = x1 + min(k + h, w)
        by = y1 + min(k + h - w + (w - max(k, 0)), h)
        if ax <= bx:
            draw.line([(ax, ay), (bx, by)], fill=color, width=1)


def _draw_rotated_text(img: Image.Image, text: str, font, color: str, cx: int, bottom_y: int):
    """Render text rotated 45° CCW, centred on cx, with bottom at bottom_y."""
    tmp_draw = ImageDraw.Draw(img)
    tw = _text_w(tmp_draw, text, font)
    th = _text_h(tmp_draw, text, font)

    txt_img = Image.new("RGBA", (tw + 5, th + 5), (0, 0, 0, 0))
    td = ImageDraw.Draw(txt_img)
    td.text((1, 1), text, font=font, fill=color)

    rotated = txt_img.rotate(0, expand=True)
    rw, rh = rotated.size

    paste_x = cx - rw // 2
    paste_y = bottom_y - rh -10

    img.paste(rotated, (paste_x, paste_y), rotated)


def _fmt_change(change: float | None, pct: float | None) -> str:
    if change is None:
        return "—"
    sign = "+" if change >= 0 else ""
    p = f" ({sign}{pct:.1f}%)" if pct is not None else ""
    return f"{sign}£{change:,.0f}{p}"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"
