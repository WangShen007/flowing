#!/usr/bin/env python3
"""Render the self-contained workflow animation used by README."""

from __future__ import annotations

import argparse
import math
from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "assets"
NAVY = (23, 59, 85)
INK = (9, 28, 43)
PANEL = (17, 47, 67)
TEAL = (36, 183, 165)
MINT = (126, 239, 214)
IVORY = (247, 248, 250)
MUTED = (158, 190, 199)
WHITE = (255, 255, 255)


def _font_candidates(bold: bool) -> list[Path]:
    if bold:
        names = (
            "Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
    else:
        names = (
            "Arial.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    return [Path(item) for item in names]


def load_font(
    size: int, explicit: str | None, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [Path(explicit)] if explicit else _font_candidates(bold)
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def scale_points(
    points: Iterable[tuple[float, float]], factor: float
) -> list[tuple[int, int]]:
    return [(round(x * factor), round(y * factor)) for x, y in points]


def rounded_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill,
    outline=None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_logo(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    """Draw the compact folded command mark without external image assets."""
    a = size * 0.22
    b = size * 0.50
    c = size * 0.78
    draw.polygon(
        [(x + a, y + b), (x + b, y + a), (x + c, y + b), (x + b, y + c)], fill=TEAL
    )
    draw.polygon(
        [
            (x + a, y + b),
            (x + b, y + c),
            (x + b, y + b * 1.18),
            (x + a * 1.55, y + b * 0.82),
        ],
        fill=NAVY,
    )
    draw.polygon(
        [
            (x + b, y + a),
            (x + c, y + b),
            (x + b * 1.18, y + b),
            (x + b * 0.72, y + a * 1.45),
        ],
        fill=IVORY,
    )


def draw_glow_path(
    image: Image.Image, points: list[tuple[int, int]], color, width: int = 3
) -> None:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.line(points, fill=(*color, 110), width=width * 7, joint="curve")
    glow = glow.filter(ImageFilter.GaussianBlur(width * 5))
    image.alpha_composite(glow)
    draw = ImageDraw.Draw(image)
    draw.line(points, fill=(*color, 235), width=width, joint="curve")


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill,
    anchor: str | None = None,
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def make_frame(
    index: int,
    total: int,
    width: int,
    height: int,
    regular_font: str | None,
    bold_font: str | None,
) -> Image.Image:
    base_w, base_h = 1200, 620
    sx, sy = width / base_w, height / base_h
    factor = min(sx, sy)
    image = Image.new("RGBA", (width, height), INK + (255,))
    draw = ImageDraw.Draw(image)

    # A quiet grid gives the dark canvas depth while keeping text readable.
    for x in range(0, base_w + 1, 40):
        px = round(x * sx)
        draw.line(
            (px, 0, px, height), fill=(17, 45, 62, 100), width=max(1, round(factor))
        )
    for y in range(0, base_h + 1, 40):
        py = round(y * sy)
        draw.line(
            (0, py, width, py), fill=(17, 45, 62, 100), width=max(1, round(factor))
        )

    def box(
        x: float,
        y: float,
        w: float,
        h: float,
        radius: float,
        fill,
        outline=None,
        line_width: int = 1,
    ) -> None:
        rounded_box(
            draw,
            (round(x * sx), round(y * sy), round((x + w) * sx), round((y + h) * sy)),
            round(radius * factor),
            fill,
            outline,
            max(1, round(line_width * factor)),
        )

    def text(
        x: float,
        y: float,
        value: str,
        size: int,
        fill,
        bold: bool = False,
        anchor: str | None = None,
    ) -> None:
        draw_text(
            draw,
            (round(x * sx), round(y * sy)),
            value,
            load_font(
                max(8, round(size * factor)), bold_font if bold else regular_font, bold
            ),
            fill,
            anchor,
        )

    text(56, 42, "FLOWING", 18, WHITE, True)
    text(56, 67, "NATURAL LANGUAGE  /  VERIFIED ACTION", 10, MUTED)
    draw_logo(draw, round(21 * sx), round(35 * sy), round(25 * factor))
    text(1140, 56, "LIVE WORKFLOW", 10, MINT, True, "ra")
    draw.ellipse(
        (round(1152 * sx), round(82 * sy), round(1158 * sx), round(88 * sy)), fill=TEAL
    )

    # User request card.
    box(54, 165, 300, 302, 18, PANEL, (44, 91, 106), 1)
    text(82, 196, "01  UNDERSTAND", 11, MINT, True)
    text(82, 234, "Find a free hour", 23, WHITE, True)
    text(82, 264, "tomorrow, then book", 23, WHITE, True)
    text(82, 294, "a project review.", 23, WHITE, True)
    text(82, 350, "intent", 10, MUTED)
    box(82, 369, 112, 28, 14, (25, 86, 89), (48, 135, 128), 1)
    text(138, 383, "CALENDAR", 10, MINT, True, "mm")
    box(204, 369, 104, 28, 14, (25, 68, 83), (48, 101, 114), 1)
    text(256, 383, "MESSAGE", 10, (186, 216, 219), True, "mm")
    text(82, 433, "Your goal becomes a traceable plan.", 11, MUTED)

    # Workflow lane and cards.
    node_y = 293
    nodes = [
        (445, "DISCOVER", "capabilities"),
        (625, "CHECK", "live data"),
        (805, "CONFIRM", "write step"),
        (985, "EXECUTE", "receipt"),
    ]
    path = [(354, node_y), *[(x, node_y) for x, _, _ in nodes], (1148, node_y)]
    draw_glow_path(image, scale_points(path, sx), TEAL, max(2, round(3 * factor)))
    draw = ImageDraw.Draw(image)

    cycle = (index / max(1, total - 1)) * len(nodes)
    for position, (x, label, detail) in enumerate(nodes):
        local = cycle - position
        active = max(0.0, min(1.0, local * 2.4))
        completed = local >= 0.72
        border = MINT if active > 0.05 or completed else (54, 98, 111)
        fill = (24, 78, 88) if active > 0.05 or completed else PANEL
        box(x - 69, 234, 138, 118, 16, fill, border, 2 if active > 0.05 else 1)
        text(
            x, 263, f"0{position + 2}", 10, MINT if active > 0.05 else MUTED, True, "mm"
        )
        text(x, 297, label, 13, WHITE if active > 0.05 else (189, 212, 216), True, "mm")
        text(x, 325, detail, 10, MINT if active > 0.05 else MUTED, False, "mm")
        if completed:
            draw.ellipse(
                (
                    round((x + 48) * sx),
                    round(246 * sy),
                    round((x + 60) * sx),
                    round(258 * sy),
                ),
                fill=TEAL,
            )
            draw.line(
                scale_points([(x + 51, 252), (x + 54, 255), (x + 58, 249)], sx),
                fill=INK,
                width=max(1, round(2 * factor)),
            )

    # Animated signal and pulse around the currently active node.
    signal_position = max(0.0, min(1.0, cycle / len(nodes)))
    signal_x = 354 + (1148 - 354) * signal_position
    pulse = 8 + 6 * (0.5 + 0.5 * math.sin(index * math.pi * 2 / max(1, total)))
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (
            round((signal_x - pulse) * sx),
            round((node_y - pulse) * sy),
            round((signal_x + pulse) * sx),
            round((node_y + pulse) * sy),
        ),
        fill=(*MINT, 80),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(round(9 * factor)))
    image.alpha_composite(glow)
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (
            round((signal_x - 6) * sx),
            round((node_y - 6) * sy),
            round((signal_x + 6) * sx),
            round((node_y + 6) * sy),
        ),
        fill=MINT,
    )

    # Result panel anchors the final state.
    box(54, 505, 1092, 64, 15, (14, 40, 56), (37, 85, 102), 1)
    text(80, 537, "TRACE", 10, MUTED, True, "lm")
    text(
        162,
        537,
        "read  →  verify  →  confirm  →  write  →  remember",
        13,
        WHITE,
        False,
        "lm",
    )
    text(1118, 537, f"{round(signal_position * 100):02d}%", 12, MINT, True, "rm")

    return image.convert("RGB")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--font", help="Regular TrueType font path")
    parser.add_argument("--bold-font", help="Bold TrueType font path")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=620)
    parser.add_argument("--frames", type=int, default=36)
    parser.add_argument(
        "--duration", type=int, default=85, help="Milliseconds per frame"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.width < 480 or args.height < 240:
        raise SystemExit("width must be at least 480 and height at least 240")
    if not 8 <= args.frames <= 120:
        raise SystemExit("frames must be between 8 and 120")
    if args.duration < 30:
        raise SystemExit("duration must be at least 30 milliseconds")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    frames = [
        make_frame(i, args.frames, args.width, args.height, args.font, args.bold_font)
        for i in range(args.frames)
    ]
    gif_path = args.output_dir / "flowing-workflow.gif"
    png_path = args.output_dir / "flowing-workflow.png"
    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=args.duration,
        loop=0,
        optimize=True,
        disposal=2,
    )
    frames[len(frames) // 2].save(png_path, format="PNG", optimize=True)
    print(f"wrote {gif_path} ({gif_path.stat().st_size} bytes)")
    print(f"wrote {png_path} ({png_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
