#!/usr/bin/env python3
"""
black_to_svg.py - detect the black parts of a JPG/PNG and trace them into a
resolution-independent SVG (true vector, not an embedded bitmap).

Pipeline:
    load -> flatten alpha -> grayscale -> (optional supersample) -> threshold
    -> marching-"crack" contour tracing -> Douglas-Peucker simplify
    -> optional Bezier smoothing -> single <path> with fill-rule="evenodd"

Only dependency: Pillow  (pip install pillow)

Examples:
    python3 black_to_svg.py logo.png
    python3 black_to_svg.py scan.jpg -o out.svg --threshold 128 --smooth 1.0
    python3 black_to_svg.py logo.png --upscale 2 --tolerance 0.6 --min-area 8
    python3 black_to_svg.py stamp.png --invert --color "#e10600" --width 2048
"""

from __future__ import annotations

import argparse
import math
import os
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.exit("Pillow is required:  pip install pillow")


# --------------------------------------------------------------------------- #
# 1. load + binarize
# --------------------------------------------------------------------------- #
def load_gray(path: str, upscale: float, max_side: int) -> Image.Image:
    """Open any JPG/PNG, composite transparency onto white, return 8-bit gray."""
    im = Image.open(path)
    im.load()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("L")

    w, h = im.size
    if max_side and max(w, h) > max_side:
        s = max_side / float(max(w, h))
        w, h = max(1, round(w * s)), max(1, round(h * s))
        im = im.resize((w, h), Image.LANCZOS)
    if upscale and upscale != 1.0:
        im = im.resize((max(1, round(w * upscale)), max(1, round(h * upscale))),
                       Image.LANCZOS)
    return im


def otsu_threshold(hist: list[int]) -> int:
    """Classic Otsu on a 256-bin histogram."""
    total = sum(hist)
    if total == 0:
        return 128
    sum_all = sum(i * hist[i] for i in range(256))
    sum_b = w_b = 0.0
    best_t, best_var = 128, -1.0
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def binarize(im: Image.Image, threshold, invert: bool):
    """Return (mask bytearray of 0/1, width, height, threshold used)."""
    if threshold in (None, "auto"):
        thr = otsu_threshold(im.histogram())
    else:
        thr = int(threshold)
    lut = [(1 if v <= thr else 0) for v in range(256)]
    if invert:
        lut = [1 - v for v in lut]
    w, h = im.size
    return bytearray(im.point(lut, "L").tobytes()), w, h, thr


# --------------------------------------------------------------------------- #
# 2. contour tracing (pixel-crack boundaries -> closed loops)
# --------------------------------------------------------------------------- #
def trace_loops(mask: bytearray, w: int, h: int, connectivity: int = 8):
    """
    Walk the boundary 'cracks' between foreground and background pixels.
    Every closed loop is returned as a list of integer lattice points.
    Outer contours and holes both come out; evenODD fill handles the holes.
    """
    out: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(a, b):
        out.setdefault(a, []).append(b)

    for y in range(h):
        row = y * w
        for x in range(w):
            if not mask[row + x]:
                continue
            if y == 0 or not mask[row - w + x]:
                add((x, y), (x + 1, y))                    # top    ->  +x
            if x == w - 1 or not mask[row + x + 1]:
                add((x + 1, y), (x + 1, y + 1))            # right  ->  +y
            if y == h - 1 or not mask[row + w + x]:
                add((x + 1, y + 1), (x, y + 1))            # bottom ->  -x
            if x == 0 or not mask[row + x - 1]:
                add((x, y + 1), (x, y))                    # left   ->  -y

    # at a pinch point two loops meet; sign decides 4- vs 8-connected shapes
    want = -1 if connectivity == 8 else 1

    def pick(cur, cands, d_in):
        if len(cands) == 1:
            return 0
        best_i, best_key = 0, None
        for i, nxt in enumerate(cands):
            d = (nxt[0] - cur[0], nxt[1] - cur[1])
            cross = d_in[0] * d[1] - d_in[1] * d[0]
            dot = d_in[0] * d[0] + d_in[1] * d[1]
            key = (0 if cross * want > 0 else 1, -dot)     # preferred turn first
            if best_key is None or key < best_key:
                best_key, best_i = key, i
        return best_i

    loops = []
    for start in list(out.keys()):
        while out.get(start):
            pts = [start]
            cur = start
            nxt = out[cur].pop()
            if not out[cur]:
                del out[cur]
            d_in = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            while cur != start:
                cands = out.get(cur)
                if not cands:                              # should not happen
                    break
                i = pick(cur, cands, d_in)
                nxt = cands.pop(i)
                if not cands:
                    del out[cur]
                pts.append(cur)
                d_in = (nxt[0] - cur[0], nxt[1] - cur[1])
                cur = nxt
            if len(pts) >= 4:
                loops.append(drop_collinear(pts))
    return loops


def drop_collinear(pts):
    n = len(pts)
    keep = []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) != (b[1] - a[1]) * (c[0] - b[0]):
            keep.append(b)
    return keep if len(keep) >= 3 else pts


def polygon_area(pts) -> float:
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


# --------------------------------------------------------------------------- #
# 3. simplify (Douglas-Peucker on a closed ring)
# --------------------------------------------------------------------------- #
def rdp(pts, eps: float):
    if eps <= 0 or len(pts) < 3:
        return list(pts)

    def _rdp(seq):
        if len(seq) < 3:
            return list(seq)
        (x1, y1), (x2, y2) = seq[0], seq[-1]
        dx, dy = x2 - x1, y2 - y1
        den = math.hypot(dx, dy)
        idx, far = 0, -1.0
        for i in range(1, len(seq) - 1):
            px, py = seq[i]
            if den == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / den
            if d > far:
                idx, far = i, d
        if far <= eps:
            return [seq[0], seq[-1]]
        return _rdp(seq[:idx + 1])[:-1] + _rdp(seq[idx:])

    # split the ring at two far-apart anchors so the closed curve is handled well
    n = len(pts)
    a = 0
    b = max(range(n), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 +
                                    (pts[i][1] - pts[0][1]) ** 2)
    if b == a:
        b = n // 2
    part1 = _rdp(pts[a:b + 1])
    part2 = _rdp(pts[b:] + [pts[0]])
    ring = part1[:-1] + part2[:-1]
    return ring if len(ring) >= 3 else list(pts)


# --------------------------------------------------------------------------- #
# 4. SVG emission
# --------------------------------------------------------------------------- #
def fmt(v: float, nd: int = 3) -> str:
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def ring_to_path(pts, scale: float, smooth: float, nd: int) -> str:
    p = [(x * scale, y * scale) for x, y in pts]
    n = len(p)
    if smooth <= 0:
        d = [f"M{fmt(p[0][0], nd)} {fmt(p[0][1], nd)}"]
        for x, y in p[1:]:
            d.append(f"L{fmt(x, nd)} {fmt(y, nd)}")
        d.append("Z")
        return "".join(d)

    # closed Catmull-Rom -> cubic Bezier
    k = smooth / 6.0
    d = [f"M{fmt(p[0][0], nd)} {fmt(p[0][1], nd)}"]
    for i in range(n):
        p0, p1, p2, p3 = p[(i - 1) % n], p[i], p[(i + 1) % n], p[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) * k, p1[1] + (p2[1] - p0[1]) * k)
        c2 = (p2[0] - (p3[0] - p1[0]) * k, p2[1] - (p3[1] - p1[1]) * k)
        d.append(f"C{fmt(c1[0], nd)} {fmt(c1[1], nd)},"
                 f"{fmt(c2[0], nd)} {fmt(c2[1], nd)},"
                 f"{fmt(p2[0], nd)} {fmt(p2[1], nd)}")
    d.append("Z")
    return "".join(d)


def build_svg(loops, w, h, scale, color, bg, smooth, nd) -> str:
    vw, vh = w * scale, h * scale
    d = "".join(ring_to_path(r, scale, smooth, nd) for r in loops)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(vw, 2)}" '
        f'height="{fmt(vh, 2)}" viewBox="0 0 {fmt(vw, 2)} {fmt(vh, 2)}">',
        '<title>traced black regions</title>',
    ]
    if bg:
        parts.append(f'<rect width="100%" height="100%" fill="{bg}"/>')
    if d:
        parts.append(f'<path fill="{color}" fill-rule="evenodd" '
                     f'shape-rendering="geometricPrecision" d="{d}"/>')
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 5. cli
# --------------------------------------------------------------------------- #
def vectorize(path, out=None, threshold="auto", invert=False, upscale=1.0,
              max_side=4000, tolerance=0.8, smooth=0.0, min_area=4.0,
              width=None, color="#000000", bg=None, connectivity=8,
              precision=3, quiet=False):
    im = load_gray(path, upscale, max_side)
    mask, w, h, thr = binarize(im, threshold, invert)

    loops = trace_loops(mask, w, h, connectivity)
    loops = [r for r in loops if polygon_area(r) >= min_area]
    loops = [rdp(r, tolerance) for r in loops]
    loops = [r for r in loops if len(r) >= 3]

    scale = 1.0 / upscale if upscale else 1.0
    if width:                                   # nominal output width in px
        scale = float(width) / w
    svg = build_svg(loops, w, h, scale, color, bg, smooth, precision)

    out = out or os.path.splitext(path)[0] + ".svg"
    with open(out, "w", encoding="utf-8") as f:
        f.write(svg)

    if not quiet:
        px = sum(mask)
        if not loops:
            print(f"warning: nothing detected - try a different --threshold "
                  f"(used {thr}), --invert, or a lower --min-area", file=sys.stderr)
        print(f"{path} -> {out}")
        print(f"  source        : {w}x{h}px (after upscale x{upscale:g})")
        print(f"  threshold     : {thr} ({'auto/Otsu' if threshold in (None,'auto') else 'manual'})"
              f"{' [inverted]' if invert else ''}")
        print(f"  black pixels  : {px} ({100.0*px/(w*h):.2f}%)")
        print(f"  contours      : {len(loops)}  "
              f"nodes: {sum(len(r) for r in loops)}")
        print(f"  svg canvas    : {fmt(w*scale,2)}x{fmt(h*scale,2)} "
              f"(vector - scales to any resolution)")
        print(f"  file size     : {os.path.getsize(out)/1024:.1f} KB")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Detect black regions in a JPG/PNG and trace them to SVG vector.")
    ap.add_argument("image", help="input .jpg / .jpeg / .png (any Pillow format)")
    ap.add_argument("-o", "--out", help="output .svg (default: alongside input)")
    ap.add_argument("-t", "--threshold", default="auto",
                    help="0-255 cutoff for 'black', or 'auto' (Otsu). default: auto")
    ap.add_argument("--invert", action="store_true",
                    help="trace the light regions instead of the black ones")
    ap.add_argument("--upscale", type=float, default=1.0,
                    help="supersample before tracing for smoother edges (e.g. 2)")
    ap.add_argument("--max-side", type=int, default=4000,
                    help="downscale huge inputs to this longest side (0 = off)")
    ap.add_argument("--tolerance", type=float, default=0.8,
                    help="path simplification in px; 0 = keep every pixel step")
    ap.add_argument("--smooth", type=float, default=0.0,
                    help="Bezier smoothing 0-1.5 (0 = crisp polygons)")
    ap.add_argument("--min-area", type=float, default=4.0,
                    help="drop specks/holes smaller than this many px^2")
    ap.add_argument("--width", type=float, default=None,
                    help="nominal SVG width in px (vector stays scale-free)")
    ap.add_argument("--color", default="#000000", help="fill color of the shapes")
    ap.add_argument("--bg", default=None, help="optional background rect color")
    ap.add_argument("--connectivity", type=int, choices=(4, 8), default=8,
                    help="how diagonally touching pixels join. default: 8")
    ap.add_argument("--precision", type=int, default=3,
                    help="decimal places in path data")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)

    thr = a.threshold if a.threshold == "auto" else int(a.threshold)
    vectorize(a.image, a.out, thr, a.invert, a.upscale, a.max_side, a.tolerance,
              a.smooth, a.min_area, a.width, a.color, a.bg, a.connectivity,
              a.precision, a.quiet)


if __name__ == "__main__":
    main()
