#!/usr/bin/env python3
# Copyright 2026 Paul Maddern (paul@arcadegeek.co.uk)
"""Render The Wild Bunch PLOT/DRAW / gunfight pictures from the snapshot.

BASIC cannot be #SIM'd for these screens. Coordinate tables at $F320,
$F35A and $F37B plus the literal follow-on PLOT/DRAW/CIRCLE commands in
lines 320, 9800 and 9870/9871 are replayed onto a 256x176 Spectrum
bitmap (Y=0 at the bottom, as in BASIC). The gunfight street is four
CHARS PRINT layers (lines 5019-5120) plus cowboy frames at 5200/5250
and the AAAGGH hit overlay (5300/5305).

Examples:
  utils/render_wildbunch_graphics.py
  utils/render_wildbunch_graphics.py --open
  utils/render_wildbunch_graphics.py TheWildBunch.z80 \\
      build/html/thewildbunch/images
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys

from PIL import Image
from skoolkit import ROM48, read_bin_file
from skoolkit.snapshot import get_snapshot

PLAY_W = 256
PLAY_H = 176  # BASIC PLOT Y range is 0..175
SCREEN_H = 192
SCALE = 2

PLOT_POINTS = 0xF320
PLOT_POINTS_COUNT = 28
DRAW_TOP_GRADE = 0xF35A
DRAW_TOP_GRADE_COUNT = 8
DRAW_ENDING = 0xF37B
DRAW_ENDING_COUNT = 29
GAME_FONT = 0xFBFE  # CHARS for glyphs set 4; holds map _ / ~ / ↑

# BASIC line headers for the gunfight street (PROG walk is LIST-protected).
GUNFIGHT_LINES = {
    5019: 0xA7CC,
    5030: 0xA973,
    5050: 0xAA72,
    5070: 0xAB9C,
    5120: 0xABE5,
    5200: 0xACF9,
    5250: 0xAD86,
    5300: 0xAEA0,
    5305: 0xAEEE,
}
# Street layers + cowboy, then optional mid-body / hit overlays.
GUNFIGHT_STREET = (5019, 5030, 5050, 5070, 5120)
GUNFIGHT_FRAMES = {
    'ready': (),                          # after 5120 — waiting to draw
    'draw1': (5200,),                     # first cowboy twitch
    'draw2': (5250,),                     # second twitch (then "HE SHOT YOU!")
    'hit': (5300, 5305),                  # you fired — AAAGGH!
}
GUNFIGHT_HORIZON = 11  # unpainted rows above → cyan; below → yellow

CHARSETS = (
    (0xF3F7, 'glyphs-1'),
    (0xF6F7, 'glyphs-2'),
    (0xF9F7, 'glyphs-3'),
    (0xFCFE, 'glyphs-4'),
)

# Spectrum ink/paper colours (dull then bright).
PALETTE = [
    (0x00, 0x00, 0x00), (0x00, 0x00, 0xD7), (0xD7, 0x00, 0x00), (0xD7, 0x00, 0xD7),
    (0x00, 0xD7, 0x00), (0x00, 0xD7, 0xD7), (0xD7, 0xD7, 0x00), (0xD7, 0xD7, 0xD7),
    (0x00, 0x00, 0x00), (0x00, 0x00, 0xFF), (0xFF, 0x00, 0x00), (0xFF, 0x00, 0xFF),
    (0x00, 0xFF, 0x00), (0x00, 0xFF, 0xFF), (0xFF, 0xFF, 0x00), (0xFF, 0xFF, 0xFF),
]


def load_game_font(mem):
    """Game font at $FBFE (CHARS after line 5070; codes 0x20-0x7F).

    Snapshot CHARS already points here. Fall back to the ROM set for
    control codes; CHR$ 128-143 still use SpectrumDisplay.block_glyph.
    """
    rom = read_bin_file(ROM48)
    font = [bytearray(rom[0x3C00 + 8 * c:0x3C00 + 8 * c + 8]) for c in range(256)]
    for code in range(0x20, 0x80):
        font[code] = bytearray(mem[GAME_FONT + 8 * code:GAME_FONT + 8 * code + 8])
    return font


def contrast_ink(paper):
    """INK 9 / PAPER 9: black on light paper, white on dark paper."""
    return 0 if (paper & 7) >= 4 else 7


def zx_arc_points(x0, y0, dx, dy, turn):
    """Yield pixel points for ZX BASIC DRAW dx,dy,turn (radians).

    Positive turn is anti-clockwise; negative is clockwise. Centre sits on
    the left-normal side of the chord by (chord/2)/tan(turn/2) so the arc
    always ends at (x0+dx,y0+dy) — the previous opposite offset broke ±PI/2
    brows/shoulders on the prisoner screen.
    """
    if abs(turn) < 1e-9:
        steps = max(abs(int(dx)), abs(int(dy)), 1)
        for i in range(steps + 1):
            t = i / steps
            yield int(round(x0 + t * dx)), int(round(y0 + t * dy))
        return
    x1, y1 = x0 + dx, y0 + dy
    chord = math.hypot(dx, dy)
    if chord < 1e-6:
        yield int(round(x1)), int(round(y1))
        return
    half = turn / 2.0
    tan_half = math.tan(half)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    nx, ny = -dy / chord, dx / chord  # left normal of directed chord
    if abs(abs(turn) - math.pi) < 1e-6 or abs(tan_half) > 1e8:
        cx, cy = mx, my
    elif abs(tan_half) < 1e-8:
        steps = max(abs(int(dx)), abs(int(dy)), 1)
        for i in range(steps + 1):
            t = i / steps
            yield int(round(x0 + t * dx)), int(round(y0 + t * dy))
        return
    else:
        dist = (chord / 2.0) / tan_half
        cx, cy = mx + nx * dist, my + ny * dist
    radius = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    steps = max(int(abs(turn) / (math.pi / 64)), abs(int(dx)), abs(int(dy)), 8)
    for i in range(steps + 1):
        a = a0 + turn * (i / steps)
        yield (int(round(cx + radius * math.cos(a))),
               int(round(cy + radius * math.sin(a))))


class SpectrumScreen:
    """Ink-only Spectrum PLOT surface (Y grows upward)."""

    def __init__(self, paper=(0xD7, 0xD7, 0xD7), ink=(0x00, 0x00, 0x00)):
        self.paper = paper
        self.ink = ink
        self.img = Image.new('RGB', (PLAY_W, PLAY_H), paper)
        self.x = 0
        self.y = 0

    def _pix(self, x, y):
        if 0 <= x < PLAY_W and 0 <= y < PLAY_H:
            self.img.putpixel((x, PLAY_H - 1 - y), self.ink)

    def plot(self, x, y):
        self.x, self.y = int(round(x)), int(round(y))
        self._pix(self.x, self.y)

    def draw_to(self, dx, dy):
        x0, y0 = self.x, self.y
        x1, y1 = x0 + dx, y0 + dy
        steps = max(abs(dx), abs(dy), 1)
        for i in range(steps + 1):
            t = i / steps
            self._pix(int(round(x0 + t * dx)), int(round(y0 + t * dy)))
        self.x, self.y = int(round(x1)), int(round(y1))

    def draw_arc(self, dx, dy, turn):
        """ZX BASIC DRAW dx,dy,turn (turn in radians)."""
        if turn == 0:
            self.draw_to(dx, dy)
            return
        for px, py in zx_arc_points(float(self.x), float(self.y), dx, dy, turn):
            self._pix(px, py)
        self.x = int(round(self.x + dx))
        self.y = int(round(self.y + dy))

    def circle(self, cx, cy, r):
        for i in range(360):
            a = math.radians(i)
            self._pix(int(round(cx + r * math.cos(a))),
                      int(round(cy + r * math.sin(a))))

    def scaled(self, scale=SCALE):
        return self.img.resize(
            (PLAY_W * scale, PLAY_H * scale), Image.NEAREST)


class SpectrumDisplay:
    """Full 256x192 display with attributes, PRINT and PLOT."""

    def __init__(self, font):
        self.font = font
        self.pixels = [[0] * PLAY_W for _ in range(SCREEN_H)]
        self.attrs = [[(0, 7, False)] * 32 for _ in range(24)]  # paper, ink, bright
        self.ink = 9
        self.paper = 7
        self.bright = False
        self.inverse = 0
        self.row = 0
        self.col = 0
        self.x = 0
        self.y = 0

    def _effective_ink(self, paper=None):
        paper = self.paper if paper is None else paper
        if self.ink == 9:
            return contrast_ink(paper)
        return self.ink & 7

    def cls(self):
        ink = self._effective_ink()
        for y in range(SCREEN_H):
            for x in range(PLAY_W):
                self.pixels[y][x] = 0
        for r in range(24):
            for c in range(32):
                self.attrs[r][c] = (self.paper & 7, ink, self.bright)
        self.row = self.col = 0

    def set_attr(self, row, col, paper=None, ink=None):
        if not (0 <= row < 24 and 0 <= col < 32):
            return
        p = self.paper if paper is None else paper
        i = self.ink if ink is None else ink
        if i == 9:
            i = contrast_ink(p)
        self.attrs[row][col] = (p & 7, i & 7, self.bright)

    @staticmethod
    def block_glyph(code):
        """Spectrum CHR$ 128-143: 2x2 quadrant blocks (4x4 pixel quarters).

        ROM docs list bit0=TR, bit1=BR, bit2=BL, bit3=TL. Empirically the
        WANTED banner in line 7001 matches bit0=TR, bit1=TL, bit2=BR,
        bit3=BL (top/bottom nibbles of the usual diagram swapped).
        """
        bits = code & 0x0F
        quads = (
            bool(bits & 0x02), bool(bits & 0x01),  # top-left, top-right
            bool(bits & 0x08), bool(bits & 0x04),  # bottom-left, bottom-right
        )
        rows = []
        for qy in range(2):
            for _ in range(4):
                left = 0xF0 if quads[qy * 2] else 0x00
                right = 0x0F if quads[qy * 2 + 1] else 0x00
                rows.append(left | right)
        return rows

    def print_char(self, ch, row=None, col=None, paper=None, ink=None):
        if row is None:
            row = self.row
        if col is None:
            col = self.col
        if col >= 32:
            col = 0
            row += 1
        if row >= 24 or col < 0:
            return
        p = self.paper if paper is None else paper
        i = self.ink if ink is None else ink
        if i == 9:
            i = contrast_ink(p)
        self.attrs[row][col] = (p & 7, i & 7, self.bright)
        code = ch if isinstance(ch, int) else ord(ch) & 0xFF
        glyph = self.block_glyph(code) if 128 <= code <= 143 else self.font[code]
        if self.inverse:
            glyph = [(~b) & 0xFF for b in glyph]
        px, py = col * 8, row * 8
        for dy, bits in enumerate(glyph):
            for dx in range(8):
                self.pixels[py + dy][px + dx] = 1 if bits & (0x80 >> dx) else 0
        self.row, self.col = row, col + 1

    def print_at(self, row, col, text, paper=None, ink=None):
        self.row, self.col = row, col
        for ch in text:
            if ch == '\n':
                self.row += 1
                self.col = 0
                continue
            self.print_char(ch, paper=paper, ink=ink)

    def print_codes(self, row, col, codes, paper=None, ink=None):
        """PRINT codes; 0x14,n toggles INVERSE like embedded BASIC controls."""
        self.row, self.col = row, col
        i = 0
        while i < len(codes):
            code = codes[i]
            if code == 0x14 and i + 1 < len(codes):
                self.inverse = codes[i + 1]
                i += 2
                continue
            self.print_char(code, paper=paper, ink=ink)
            i += 1

    def tab(self, col):
        if col < self.col:
            self.row += 1
        self.col = col

    def _pix(self, x, y):
        # BASIC PLOT: Y=0 at bottom of 176-line playfield (rows 0..21).
        if 0 <= x < PLAY_W and 0 <= y < PLAY_H:
            self.pixels[PLAY_H - 1 - y][x] = 1

    def plot(self, x, y):
        self.x, self.y = int(round(x)), int(round(y))
        self._pix(self.x, self.y)

    def draw_to(self, dx, dy):
        x0, y0 = self.x, self.y
        steps = max(abs(dx), abs(dy), 1)
        for i in range(steps + 1):
            t = i / steps
            self._pix(int(round(x0 + t * dx)), int(round(y0 + t * dy)))
        self.x = int(round(x0 + dx))
        self.y = int(round(y0 + dy))

    def draw_arc(self, dx, dy, turn):
        """ZX BASIC DRAW dx,dy,turn (turn in radians)."""
        if turn == 0:
            self.draw_to(dx, dy)
            return
        for px, py in zx_arc_points(float(self.x), float(self.y), dx, dy, turn):
            self._pix(px, py)
        self.x = int(round(self.x + dx))
        self.y = int(round(self.y + dy))

    def circle(self, cx, cy, r):
        for i in range(360):
            a = math.radians(i)
            self._pix(int(round(cx + r * math.cos(a))),
                      int(round(cy + r * math.sin(a))))

    def image(self, scale=SCALE):
        img = Image.new('RGB', (PLAY_W, SCREEN_H))
        pix = img.load()
        for r in range(24):
            for c in range(32):
                paper, ink, bright = self.attrs[r][c]
                base = 8 if bright else 0
                paper_rgb = PALETTE[base + paper]
                ink_rgb = PALETTE[base + ink]
                for dy in range(8):
                    for dx in range(8):
                        bit = self.pixels[r * 8 + dy][c * 8 + dx]
                        pix[c * 8 + dx, r * 8 + dy] = ink_rgb if bit else paper_rgb
        if scale != 1:
            img = img.resize((PLAY_W * scale, SCREEN_H * scale), Image.NEAREST)
        return img


class GunfightDisplay:
    """PRINT/PLOT simulator for the gunfight CHARS layers.

    CHARS is switched by POKE 23606/23607. Embedded 0x10-0x17 controls and
    out-of-string colour keys are applied. OVER XORs ink pixels.
    """

    def __init__(self, mem, rom):
        self.mem = mem
        self.rom = rom
        self.pixels = [[0] * PLAY_W for _ in range(SCREEN_H)]
        self.attrs = [[(0, 7, False)] * 32 for _ in range(24)]
        self.painted = [[False] * 32 for _ in range(24)]
        self.ink = 7
        self.paper = 0
        self.bright = False
        self.over = 0
        self.inverse = 0
        self.row = 0
        self.col = 0
        self.chars = 0x3C00
        self.x = 0
        self.y = 0

    def _eff_ink(self):
        if self.ink == 9:
            return contrast_ink(self.paper)
        return self.ink & 7

    def cls(self):
        ink = self._eff_ink()
        for y in range(SCREEN_H):
            for x in range(PLAY_W):
                self.pixels[y][x] = 0
        for r in range(24):
            for c in range(32):
                self.attrs[r][c] = (self.paper & 7, ink, self.bright)
                self.painted[r][c] = False
        self.row = self.col = 0

    def fill_sky_ground(self, horizon=GUNFIGHT_HORIZON):
        """Paint cells the PRINT layers never touch.

        Line 5004 does PAPER 0: CLS, and 5019-5120 only cover part of the
        screen. On a real run the remaining sky/street read as cyan/yellow
        (screenshots); fill unpainted cells to match.
        """
        for r in range(24):
            paper = 5 if r < horizon else 6
            for c in range(32):
                if not self.painted[r][c]:
                    self.attrs[r][c] = (paper, 0, False)

    def glyph(self, code):
        if 128 <= code <= 143:
            return SpectrumDisplay.block_glyph(code)
        addr = self.chars + 8 * code
        if 0 <= addr <= 0xFFF8:
            return [self.mem[addr + i] for i in range(8)]
        return [self.rom[0x3C00 + 8 * code + i] for i in range(8)]

    def print_char(self, code):
        if self.col >= 32:
            self.col = 0
            self.row += 1
        if self.row >= 24:
            return
        ink = self._eff_ink()
        paper = self.paper & 7
        self.attrs[self.row][self.col] = (paper, ink, self.bright)
        self.painted[self.row][self.col] = True
        g = self.glyph(code)
        if self.inverse:
            g = [(~b) & 0xFF for b in g]
        px, py = self.col * 8, self.row * 8
        for dy, bits in enumerate(g):
            for dx in range(8):
                on = 1 if bits & (0x80 >> dx) else 0
                if self.over:
                    if on:
                        self.pixels[py + dy][px + dx] ^= 1
                else:
                    self.pixels[py + dy][px + dx] = on
        self.col += 1

    def emit_string(self, data):
        i = 0
        while i < len(data):
            c = data[i]
            if c == 0x10 and i + 1 < len(data):
                self.ink = data[i + 1]
                i += 2
                continue
            if c == 0x11 and i + 1 < len(data):
                self.paper = data[i + 1]
                i += 2
                continue
            if c == 0x12 and i + 1 < len(data):
                i += 2
                continue
            if c == 0x13 and i + 1 < len(data):
                self.bright = bool(data[i + 1])
                i += 2
                continue
            if c == 0x14 and i + 1 < len(data):
                self.inverse = data[i + 1]
                i += 2
                continue
            if c == 0x15 and i + 1 < len(data):
                self.over = data[i + 1]
                i += 2
                continue
            if c == 0x16 and i + 2 < len(data):
                self.row, self.col = data[i + 1], data[i + 2]
                i += 3
                continue
            if c == 0x17 and i + 1 < len(data):
                col = data[i + 1]
                if col < self.col:
                    self.row += 1
                self.col = col
                i += 2
                continue
            self.print_char(c)
            i += 1

    def plot(self, x, y):
        self.x, self.y = int(x), int(y)
        if 0 <= self.x < PLAY_W and 0 <= self.y < PLAY_H:
            self.pixels[PLAY_H - 1 - self.y][self.x] = 1

    def draw_to(self, dx, dy):
        x0, y0 = self.x, self.y
        steps = max(abs(dx), abs(dy), 1)
        for i in range(steps + 1):
            t = i / steps
            px = int(round(x0 + t * dx))
            py = int(round(y0 + t * dy))
            if 0 <= px < PLAY_W and 0 <= py < PLAY_H:
                self.pixels[PLAY_H - 1 - py][px] = 1
        self.x = int(round(x0 + dx))
        self.y = int(round(y0 + dy))

    def image(self, scale=SCALE):
        img = Image.new('RGB', (PLAY_W, SCREEN_H))
        pix = img.load()
        for r in range(24):
            for c in range(32):
                paper, ink, bright = self.attrs[r][c]
                base = 8 if bright else 0
                paper_rgb = PALETTE[base + paper]
                ink_rgb = PALETTE[base + ink]
                for dy in range(8):
                    for dx in range(8):
                        bit = self.pixels[r * 8 + dy][c * 8 + dx]
                        pix[c * 8 + dx, r * 8 + dy] = ink_rgb if bit else paper_rgb
        if scale != 1:
            img = img.resize((PLAY_W * scale, SCREEN_H * scale), Image.NEAREST)
        return img


def _gunfight_read_int(body, i, apply_ctrl):
    """Read a BASIC numeric literal; colour controls may sit before 0x0E."""
    while i < len(body) and body[i] == 0x20:
        i += 1
    neg = False
    if i < len(body) and body[i] == 0x2D:
        neg = True
        i += 1
    if i < len(body) and body[i] == 0xB0:  # VAL "n"
        i += 1
        if body[i] != 0x22:
            raise ValueError('VAL string', i)
        i += 1
        chars = []
        while body[i] != 0x22:
            chars.append(chr(body[i]))
            i += 1
        val = int(''.join(chars))
        return (-val if neg else val), i + 1
    start = i
    while i < len(body) and 0x30 <= body[i] <= 0x39:
        i += 1
    while i < len(body) and body[i] in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15):
        apply_ctrl(body[i], body[i + 1])
        i += 2
    if i < len(body) and body[i] == 0x0E:
        if body[i + 1] == 0 and body[i + 2] == 0:
            val = body[i + 3] + 256 * body[i + 4]
            if val >= 0x8000:
                val -= 0x10000
        else:
            val = int(bytes(body[start:i]).decode()) if start < i else 0
        return (-val if neg else val), i + 6
    if start < i:
        val = int(bytes(body[start:i]).decode())
        return (-val if neg else val), i
    raise ValueError('num', i, body[i:i + 12])


def _gunfight_read_string(body, i):
    """Read a quoted string; "" inside becomes a single quote character."""
    if body[i] != 0x22:
        raise ValueError('string', i)
    i += 1
    out = []
    while i < len(body):
        if body[i] == 0x22:
            if i + 1 < len(body) and body[i + 1] == 0x22:
                out.append(0x22)
                i += 2
                continue
            return out, i + 1
        out.append(body[i])
        i += 1
    return out, i


def _gunfight_exec_line(d, mem, line_no):
    addr = GUNFIGHT_LINES[line_no]
    length = mem[addr + 2] + 256 * mem[addr + 3]
    body = list(mem[addr + 4:addr + 4 + length])
    i = 0

    def apply_ctrl(code, val):
        if code == 0x10:
            d.ink = val
        elif code == 0x11:
            d.paper = val
        elif code == 0x13:
            d.bright = bool(val)
        elif code == 0x14:
            d.inverse = val
        elif code == 0x15:
            d.over = val

    def rind(idx):
        return _gunfight_read_int(body, idx, apply_ctrl)

    while i < len(body):
        b = body[i]
        if b == 0x0D:
            break
        if b in (0x3A, 0x20):
            i += 1
            continue
        if b == 0xF4:  # POKE
            i += 1
            addr_v, i = rind(i)
            if body[i] != 0x2C:
                raise ValueError('POKE comma', i)
            i += 1
            val, i = rind(i)
            if addr_v == 23606:
                d.chars = (d.chars & 0xFF00) | (val & 0xFF)
            elif addr_v == 23607:
                d.chars = (d.chars & 0x00FF) | ((val & 0xFF) << 8)
            continue
        if b == 0xF5:  # PRINT
            i += 1
            while i < len(body) and body[i] not in (0x3A, 0x0D):
                if body[i] == 0x20:
                    i += 1
                    continue
                if body[i] == 0xAC:  # AT
                    i += 1
                    row, i = rind(i)
                    if body[i] != 0x2C:
                        raise ValueError('AT comma', i)
                    i += 1
                    col, i = rind(i)
                    d.row, d.col = row, col
                    continue
                if body[i] == 0xAD:  # TAB
                    i += 1
                    col, i = rind(i)
                    if col >= 32:
                        d.col = 0
                        d.row += 1
                    else:
                        if col < d.col:
                            d.row += 1
                        d.col = col
                    continue
                if body[i] == 0x22:
                    s, i = _gunfight_read_string(body, i)
                    d.emit_string(s)
                    continue
                if body[i] == 0x3B:
                    i += 1
                    continue
                if body[i] == 0x2C:
                    if d.col < 16:
                        d.col = 16
                    else:
                        d.col = 0
                        d.row += 1
                    i += 1
                    continue
                if body[i] in (0xD9, 0xDA):  # INK / PAPER
                    tok = body[i]
                    i += 1
                    val, i = rind(i)
                    if tok == 0xD9:
                        d.ink = val
                    else:
                        d.paper = val
                    continue
                if body[i] in (0x10, 0x11, 0x12, 0x13, 0x14, 0x15) and i + 1 < len(body):
                    apply_ctrl(body[i], body[i + 1])
                    i += 2
                    continue
                raise ValueError(f'PRINT item {body[i]:02X} in line {line_no}')
            continue
        if b == 0xF6:  # PLOT
            i += 1
            x, i = rind(i)
            if body[i] != 0x2C:
                raise ValueError('PLOT comma', i)
            i += 1
            y, i = rind(i)
            d.plot(x, y)
            continue
        if b == 0xFC:  # DRAW
            i += 1
            dx, i = rind(i)
            if body[i] != 0x2C:
                raise ValueError('DRAW comma', i)
            i += 1
            dy, i = rind(i)
            d.draw_to(dx, dy)
            continue
        raise ValueError(f'statement {b:02X} in line {line_no}')


def render_gunfight(mem, frame='ready'):
    """Gunfight street frames from BASIC lines 5019-5305.

    frame: ready | draw1 | draw2 | hit (see GUNFIGHT_FRAMES).
    """
    if frame not in GUNFIGHT_FRAMES:
        raise ValueError(f'unknown gunfight frame: {frame!r}')
    rom = read_bin_file(ROM48)
    d = GunfightDisplay(mem, rom)
    d.paper = 0
    d.ink = 0
    d.cls()
    for line_no in GUNFIGHT_STREET + GUNFIGHT_FRAMES[frame]:
        _gunfight_exec_line(d, mem, line_no)
    d.fill_sky_ground()
    return d


def signed_peek(mem, addr):
    """PEEK then treat 128..255 as DRAW deltas the ROM would reject;

    these tables only store small unsigned deltas, so return as-is.
    """
    return mem[addr]


def plot_table(screen, mem, addr, count):
    for i in range(count):
        a = addr + i * 2
        screen.plot(mem[a], mem[a + 1])


def draw_segments(screen, mem, addr, count):
    # BASIC 9900: PLOT PEEK X,PEEK (X+1): DRAW PEEK (X+2),PEEK (X+3)
    for i in range(count):
        a = addr + i * 4
        screen.plot(mem[a], mem[a + 1])
        screen.draw_to(signed_peek(mem, a + 2), signed_peek(mem, a + 3))


def paint_score_footer(d, score=6412, best=0, by_name='POBTASTIC'):
    """Line 9525: score / best / by / INPUT prompt.

    PRINT blank lines separate the score/best/by rows; INPUT then shows
    the prompt on the lower screen (row 23 here), matching the gap above
    BY.
    """
    d.ink = 9
    d.print_at(17, 5, f'YOU HAVE SCORED {score}')
    d.row, d.col = 19, 0
    d.tab(4)
    d.print_at(d.row, d.col, f'BEST SCORE SO FAR IS {best}')
    d.row, d.col = 21, 0
    d.tab(4)
    d.print_at(d.row, d.col, f'BY {by_name}')
    prompt = 'PRESS ENTER FOR ANOTHER GAME'
    d.print_at(23, 0, prompt)
    d.print_char(0x8F, row=23, col=len(prompt))


def render_top_grade(mem, score=6412, best=0, by_name='POBTASTIC',
                     grade='HERO!'):
    """Line 9800 star badge + 9525 score footer (matches the red end screen)."""
    R = 2
    d = SpectrumDisplay(load_game_font(mem))
    d.paper = R
    d.ink = 9
    d.cls()

    # Line 9800: GO SUB 9900 with X=$F35A,T=8, then literal DRAWs.
    draw_segments(d, mem, DRAW_TOP_GRADE, DRAW_TOP_GRADE_COUNT)
    d.draw_to(-17, 29)
    d.plot(139, 137)
    d.draw_to(-16, 28)
    d.plot(73, 137)
    d.draw_to(17, -30)
    d.plot(107, 78)
    d.draw_to(16, -30)

    d.print_at(6, 14, 'TOP')
    d.print_at(8, 13, 'GRADE')
    d.print_at(10, 13, grade)
    paint_score_footer(d, score=score, best=best, by_name=by_name)
    return d


def render_ending(mem, score=0, best=0, by_name='POBTASTIC'):
    """Lines 9870-9871: prisoner in jail window on red, plus 9525 footer.

    LIST shows empty strings; the snapshot has CHR$ 143/138 block graphics
    for the black frame and vertical bars.
    """
    R = 2
    # Line 9870 strings are 13 bytes (LIST shows empty / spaces).
    frame_top = bytes([0x8F] * 13)    # AT 2,9
    frame_bot = bytes([0x8F] * 13)    # AT 14,9
    # full, gap, bar, gap, bar, gap, bar, gap, full
    bars = bytes([0x8F, 0x20, 0x20, 0x8A, 0x20, 0x20, 0x8A,
                  0x20, 0x20, 0x8A, 0x20, 0x20, 0x8F])
    d = SpectrumDisplay(load_game_font(mem))
    d.paper = R
    d.ink = 0
    d.cls()

    d.print_codes(2, 9, frame_top, paper=R, ink=0)
    d.print_codes(14, 9, frame_bot, paper=R, ink=0)
    for n in range(3, 14):
        d.print_codes(n, 9, bars, paper=5, ink=0)

    d.ink = 0
    draw_segments(d, mem, DRAW_ENDING, DRAW_ENDING_COUNT)
    plot_table(d, mem, PLOT_POINTS, PLOT_POINTS_COUNT)
    d.plot(108, 95)
    d.draw_arc(28, 0, math.pi)
    d.plot(126, 73)
    d.draw_to(-3, 1)  # DRAW -3,R with R=1
    d.plot(116, 86)
    d.draw_arc(12, 0, -math.pi)
    d.plot(127, 110)
    d.draw_arc(5, 3, -math.pi / 2)
    d.plot(117, 110)
    d.draw_arc(-5, 3, math.pi / 2)
    d.plot(147, 72)
    d.draw_arc(6, -8, -math.pi / 2)
    d.plot(97, 72)
    d.draw_arc(-6, -8, math.pi / 2)

    paint_score_footer(d, score=score, best=best, by_name=by_name)
    return d


def render_rip(mem, score=728, best=728, by_name='POBTASTIC'):
    """Line 9850: black cross from S$=CHR$ 143 x3, R.I.P, plus 9525 footer."""
    R = 2
    block = bytes([0x8F, 0x8F, 0x8F])  # LIST shows LET S$="" — blocks hidden
    d = SpectrumDisplay(load_game_font(mem))
    d.paper = R
    d.ink = 0
    d.cls()

    # Vertical stem: AT N,13;S$ for N=1..13 (cols 13-15).
    for n in range(1, 14):
        d.print_codes(n, 13, block, paper=R, ink=0)
    # Crossbar: AT N,10;S$;S$;S$ for N=4..6 (cols 10-18).
    for n in range(4, 7):
        d.print_codes(n, 10, block * 3, paper=R, ink=0)

    # INK 9: PRINT PAPER 0;AT 5,12;"R.I.P"
    d.ink = 9
    d.print_at(5, 12, 'R.I.P', paper=0, ink=9)
    paint_score_footer(d, score=score, best=best, by_name=by_name)
    return d


def render_line_320(mem):
    # Line 320 town-map flourishes (literals only; CIRCLE moon).
    s = SpectrumScreen()
    s.plot(92, 136)
    s.draw_to(5, 9)
    s.draw_to(3, -5)  # INT PI == 3
    s.plot(97, 136)
    s.draw_to(5, 7)
    s.draw_to(5, -7)
    s.plot(105, 139)
    s.draw_to(3, 6)
    s.draw_to(5, -9)
    s.plot(111, 140)
    s.draw_to(5, 4)
    s.draw_to(6, -8)
    s.circle(219, 144, 12)
    return s


def render_town_map(mem):
    """BASIC lines 310-325: town map with ~ border, cacti, towns, legend."""
    U, R = 1, 2
    d = SpectrumDisplay(load_game_font(mem))
    d.ink = 9
    d.paper = R
    d.cls()

    # Line 310: ~ frame and yellow interior.
    for n in range(U, 31):
        d.print_at(U, n, '~')
        d.print_at(11, n, '~')
    spaces = ' ' * 28
    for n in range(R, 11):
        d.print_at(n, U, '~', paper=R)
        d.paper = 6
        d.print_at(n, R, spaces, paper=6)
        d.paper = R
        d.print_at(n, 30, '~', paper=R)

    # Line 315: cacti (_), town numbers with CHR$ 129/130/135 markers
    # (LIST hides the blocks / INVERSE), compass N/↑.
    d.paper = 6
    d.print_at(7, 6, '_', paper=6)
    d.print_codes(2, 9, bytes([ord('4'), 0x14, 1, 0x14, 0, 0x82]), paper=6)
    d.print_codes(
        5, 3,
        bytes([ord('5'), 0x14, 1, 0x87, 0x14, 0, 0x20, 0x20, 0x20, ord('_')]),
        paper=6)
    d.print_codes(6, 9, bytes([ord('_'), 0x20, 0x20, 0x20, 0x81, ord('3')]),
                  paper=6)
    d.tab(21)
    d.print_codes(d.row, d.col, bytes([0x81, ord('1')]), paper=6)
    d.print_codes(9, 10, bytes([0x81, ord('2')]), paper=6)
    d.print_at(3, 27, 'N', paper=6)
    d.print_at(4, 27, '\x5e', paper=6)  # ↑ is CHR$ 94

    # Line 320: mountains + compass circle.
    d.plot(92, 136)
    d.draw_to(5, 9)
    d.draw_to(3, -5)
    d.plot(97, 136)
    d.draw_to(5, 7)
    d.draw_to(5, -7)
    d.plot(105, 139)
    d.draw_to(3, 6)
    d.draw_to(5, -9)
    d.plot(111, 140)
    d.draw_to(5, 4)
    d.draw_to(6, -8)
    d.circle(219, 144, 12)

    # Line 325: legend on red.
    d.paper = R
    d.print_at(13, R, '1=DRY GULCH : 2=DODGE CITY', paper=R)
    d.print_at(14, R, '3=NUGGET CITY : 4=BULLETVILLE', paper=R)
    d.print_at(15, 8, '5=DEADMANS CREEK', paper=R)
    d.print_at(16, 7, '------------------', paper=R)
    return d


def paint_wanted_face(s, feature=None):
    """Lines 7010-7065 base face, plus optional D$(L) feature extras."""
    R, O = 1, 0
    s.plot(117, 135)
    s.draw_to(-7, -17)
    s.draw_to(18, -10)
    s.draw_to(O, 15)
    s.draw_to(10, 12)
    s.draw_to(8, -17)
    s.draw_to(-18, -10)
    s.plot(127, 123)
    s.draw_to(-10, 12)
    s.draw_to(10, -9)
    s.draw_to(11, 9)
    s.plot(145, 120)
    s.draw_to(15, -R)
    s.draw_to(-32, -12)
    s.plot(127, 106)
    s.draw_to(-32, 12)
    s.draw_to(15, R)
    s.plot(111, 112)
    s.draw_to(6, -30)
    s.plot(145, 112)
    s.draw_to(-6, -30)
    s.draw_arc(-22, O, -math.pi)
    if feature != 'NO RIGHT EAR.......':
        s.plot(112, 108)
        s.draw_arc(-4, O, math.pi)
        s.draw_to(6, -13)
    if feature != 'NO LEFT EAR........':
        s.plot(144, 108)
        s.draw_arc(4, O, -math.pi)
        s.draw_to(-6, -13)
    s.plot(132, 82)
    s.plot(131, 83)
    s.draw_to(-7, O)
    s.plot(123, 82)
    for y in (90, 90, 92, 95, 97, 98, 101):
        s.plot(126 if y == 90 else 127, y)
        if y == 90:
            s.plot(128, 90)
    s.plot(116, 104)
    s.plot(116, 105)
    s.draw_to(3, O)
    s.plot(119, 104)
    s.plot(120, 104)
    s.plot(119, 105)
    s.plot(138, 105)
    s.draw_to(-3, O)
    s.plot(136, 104)
    s.draw_to(-3, 0)
    s.plot(135, 106)
    s.plot(139, 104)
    s.plot(134, 101)
    s.draw_to(3, O)
    s.plot(133, 102)
    s.plot(123, 102)
    s.plot(119, 101)
    s.draw_to(3, O)
    if feature != 'NO EYEBROWS........':
        s.plot(132, 105)
        s.plot(131, 105)
        s.plot(131, 104)
        s.plot(135, 107)
        s.draw_to(4, O)
        s.plot(132, 106)
        s.draw_to(4, O)
        s.plot(123, 104)
        s.plot(123, 105)
        s.plot(122, 105)
        s.plot(119, 106)
        s.draw_to(4, O)
        s.plot(116, 107)
        s.draw_to(5, O)
    s.plot(114, 95)
    s.draw_to(O, 16)
    s.plot(113, 98)
    s.draw_to(O, 13)
    s.plot(112, 105)
    s.draw_to(O, 7)
    s.plot(142, 96)
    s.draw_to(O, 15)
    s.plot(143, 98)
    s.draw_to(O, 13)
    s.plot(144, 105)
    s.draw_to(O, 7)
    s.plot(138, 70)
    s.draw_to(O, 10)
    s.plot(117, 70)
    s.draw_to(O, 11)
    s.plot(100, 64)
    s.draw_to(14, O)
    s.draw_to(O, 6)
    s.plot(116, 71)
    s.plot(115, 71)
    s.draw_to(8, -4)
    s.draw_to(O, -9)
    s.plot(121, 66)
    s.plot(120, 65)
    s.draw_to(O, -3)
    s.plot(119, 61)
    s.plot(118, 61)
    s.draw_to(-3, 3)
    s.plot(132, 58)
    s.draw_to(O, 9)
    s.draw_to(7, 3)
    s.plot(133, 67)
    s.draw_to(3, -6)
    s.draw_to(4, 3)
    s.draw_to(O, 5)
    s.plot(140, 64)
    s.draw_to(12, O)
    if feature == 'SCAR ON RIGHT CHEEK':
        s.plot(119, 89)
        s.draw_to(3, 7)
    elif feature == 'SCAR ON LEFT CHEEK.':
        s.plot(136, 89)
        s.draw_to(-3, 7)
    elif feature == 'A LEFT EARING......':
        for x, y in ((147, 104), (148, 103), (148, 102), (147, 101)):
            s.plot(x, y)
    elif feature == 'A RIGHT EARING.....':
        for x, y in ((109, 103), (109, 101), (108, 102), (108, 103)):
            s.plot(x, y)
    elif feature == 'A MOUSTACHE........':
        s.plot(128, 87)
        s.draw_to(4, O)
        s.plot(128, 86)
        s.draw_to(4, O)
        s.plot(128, 85)
        s.draw_to(5, O)
        s.plot(134, 84)
        s.plot(133, 84)
        s.plot(126, 87)
        s.draw_to(-3, O)
        s.plot(126, 86)
        s.draw_to(-4, O)
        s.plot(126, 85)
        s.draw_to(-4, O)
        s.plot(121, 84)
        s.plot(122, 84)
    elif feature == 'PATCH ON LEFT EYE..':
        for y, w in ((101, 5), (102, 6), (103, 7), (104, 7)):
            s.plot(132, y)
            s.draw_to(w, O)
        s.plot(129, 105)
        s.draw_to(10, O)
        s.plot(133, 106)
        s.draw_to(6, O)
        s.plot(135, 107)
        s.draw_to(4, O)
        s.plot(140, 108)
        s.plot(141, 108)
    elif feature == 'PATCH ON RIGHT EYE.':
        s.plot(115, 107)
        s.draw_to(6, O)
        for y, w in ((106, 10), (105, 7), (104, 7), (103, 7), (102, 7)):
            s.plot(116, y)
            s.draw_to(w, O)
        s.plot(117, 101)
        s.draw_to(5, O)


# D$(L) strings from DATA 9001-9012. Limps only change DESCRIPTION text.
WANTED_FACE_FEATURES = (
    'PATCH ON RIGHT EYE.',
    'SCAR ON LEFT CHEEK.',
    'NO LEFT EAR........',
    'PATCH ON LEFT EYE..',
    'SCAR ON RIGHT CHEEK',
    'A LEFT EARING......',
    'NO EYEBROWS........',
    'NO RIGHT EAR.......',
    'A MOUSTACHE........',
    'A RIGHT EARING.....',
)
WANTED_TEXT_ONLY_FEATURES = (
    'A LIMP IN LEFT LEG.',
    'A LIMP IN RIGHT LEG',
)


def wanted_feature_slug(feature):
    """Filename slug for a D$(L) face feature."""
    return (feature.rstrip('.')
            .strip()
            .lower()
            .replace(' ', '-'))


def render_wanted_face(mem, feature=None):
    s = SpectrumScreen()
    paint_wanted_face(s, feature=feature)
    return s


# Line 7001 WANTED banner (block graphics CHR$ 128-143; LIST shows spaces).
WANTED_BANNER = (
    [32, 32, 32, 140, 136, 140, 136, 32, 132, 140, 32, 32, 140, 32, 140, 136,
     132, 140, 140, 140, 32, 140, 140, 140, 136, 132, 140, 140],
    [32, 32, 32, 133, 132, 133, 32, 32, 138, 32, 138, 32, 133, 134, 133, 32,
     133, 32, 138, 133, 32, 133, 32, 32, 138, 32, 138, 32, 134],
    [32, 32, 32, 133, 133, 133, 32, 32, 139, 131, 138, 32, 133, 133, 133, 32,
     32, 32, 138, 32, 32, 133, 131, 130, 136, 32, 138, 32, 133],
    [32, 32, 32, 133, 130, 135, 32, 132, 142, 132, 142, 32, 141, 136, 135, 32,
     32, 132, 142, 32, 32, 141, 140, 140, 138, 132, 142, 140, 130],
)

# Sample posters: base R() from line 9095, often +50 per telegraph/jailbreak.
# Body lines are hand-placed to match screenshots (TAB/comma wrap in 7110-7130).
WANTED_POSTERS = {
    'longbough': {
        # L=2, D$ limp; $150 = base 100 + one +50 bump.
        'feature': 'A LIMP IN LEFT LEG.',
        'reward': 150,
        'body': (
            (15, 9, 'HARRY LONGBOUGH'),
            (16, 6, 'ALIAS-THE SUNDANCE KID'),
            (17, 2, 'FOR-MURDER:HORSE AND CATTLE'),
            (18, 1, 'RUSTLING:BANK AND TRAIN ROBBERY'),
            (20, 1, 'DESCRIPTION-LIMPS WITH LEFT LEG'),
        ),
    },
    'logan-eyepatch': {
        # L=3, D$ left-eye patch; $250 = base 100 + three +50 bumps.
        'feature': 'PATCH ON LEFT EYE..',
        'reward': 250,
        'body': (
            (15, 10, 'HARVEY LOGAN'),
            (16, 2, 'ALIAS-KID CURRY:TOM CAPEHART:'),
            (17, 2, 'BOB NEVILLES'),
            (18, 3, 'FOR-MURDER:TRAIN ROBBERY'),
            (20, 1, 'DESCRIPTION-SEE PICTURE'),
        ),
    },
}


def render_wanted_poster(mem, variant='longbough'):
    """Lines 7000-7140: WANTED poster. Face extras come from D$(L)."""
    spec = WANTED_POSTERS[variant]
    d = SpectrumDisplay(load_game_font(mem))
    d.ink = 9
    d.paper = 7
    d.cls()

    for row, codes in enumerate(WANTED_BANNER):
        d.print_codes(row, 0, codes, paper=7)

    reward = spec['reward']
    d.print_at(7, 4, 'DEAD', paper=7)
    d.print_at(7, 23, 'DEAD', paper=7)
    d.print_at(9, 5, 'OR', paper=7)
    d.print_at(9, 24, 'OR', paper=7)
    d.print_at(11, 4, 'ALIVE', paper=7)
    d.print_at(11, 23, 'ALIVE', paper=7)
    d.print_at(13, 4, f'${reward}', paper=7)
    d.print_at(13, 23, f'${reward}', paper=7)

    paint_wanted_face(d, feature=spec['feature'])

    for row, col, text in spec['body']:
        d.print_at(row, col, text, paper=7)

    # Line 7140 INPUT uses the lower screen; screenshot shows a red strip.
    for row in (21, 22, 23):
        for col in range(32):
            d.attrs[row][col] = (2, 7, False)
            for dy in range(8):
                for dx in range(8):
                    d.pixels[row * 8 + dy][col * 8 + dx] = 0
    d.print_at(22, 3, 'PRESS ENTER TO CONTINUE', paper=2, ink=7)
    d.print_char(143, row=22, col=26, paper=2, ink=7)
    return d


def render_charset(mem, addr, scale=2):
    """96 glyphs (codes 32-127) as a 16x6 sheet."""
    cols, rows = 16, 6
    cell = 8 * scale
    sheet = Image.new('RGB', (cols * cell, rows * cell), (0xD7, 0xD7, 0xD7))
    ink = (0x00, 0x00, 0x00)
    for n in range(96):
        gx, gy = n % cols, n // cols
        base = addr + n * 8
        for row in range(8):
            bits = mem[base + row]
            for col in range(8):
                if bits & (0x80 >> col):
                    x0 = gx * cell + col * scale
                    y0 = gy * cell + row * scale
                    for dy in range(scale):
                        for dx in range(scale):
                            sheet.putpixel((x0 + dx, y0 + dy), ink)
    return sheet


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Render Wild Bunch PLOT/DRAW graphics from the snapshot.')
    parser.add_argument(
        'snapshot', nargs='?', default='TheWildBunch.z80',
        help='Snapshot path (default: TheWildBunch.z80)')
    parser.add_argument(
        'output_dirs', nargs='*',
        help='One or more directories for PNG output')
    parser.add_argument(
        '-o', '--output-dir', action='append', default=[],
        help='Extra PNG output directory (repeatable)')
    parser.add_argument(
        '--fonts', action='store_true',
        help='Also write the four glyph sheets')
    parser.add_argument(
        '--open', action='store_true',
        help='Open the rendered PNGs (macOS open / xdg-open)')
    parser.add_argument(
        '--scale', type=int, default=SCALE, help='Pixel scale (default: 2)')
    args = parser.parse_args(argv)

    snap = args.snapshot
    if not os.path.isfile(snap):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        alt = os.path.join(here, snap)
        if os.path.isfile(alt):
            snap = alt
        else:
            sys.stderr.write(f'{snap}: snapshot not found\n')
            sys.exit(1)

    out_dirs = list(args.output_dirs) + list(args.output_dir)
    if not out_dirs:
        out_dirs = ['build/graphics']

    mem = get_snapshot(snap)
    scale = args.scale
    written = []

    pictures = [
        ('town-map.png', render_town_map),
        ('wanted-poster.png',
         lambda m: render_wanted_poster(m, 'longbough')),
        ('wanted-poster-eyepatch.png',
         lambda m: render_wanted_poster(m, 'logan-eyepatch')),
        ('top-grade.png', render_top_grade),
        ('ending.png', render_ending),
        ('rip.png', render_rip),
        ('wanted-face.png', lambda m: render_wanted_face(m)),
        ('gunfight.png', lambda m: render_gunfight(m, 'ready')),
        ('gunfight-draw1.png', lambda m: render_gunfight(m, 'draw1')),
        ('gunfight-draw2.png', lambda m: render_gunfight(m, 'draw2')),
        ('gunfight-aaaggh.png', lambda m: render_gunfight(m, 'hit')),
    ]
    for feature in WANTED_FACE_FEATURES:
        slug = wanted_feature_slug(feature)
        pictures.append(
            (f'wanted-face-{slug}.png',
             lambda m, f=feature: render_wanted_face(m, f)))
    rendered = []
    for name, fn in pictures:
        out = fn(mem)
        if hasattr(out, 'image'):
            rendered.append((name, out.image(scale)))
        else:
            rendered.append((name, out.scaled(scale)))
    if args.fonts:
        for addr, name in CHARSETS:
            rendered.append((f'{name}.png', render_charset(mem, addr, scale=scale)))

    for out in out_dirs:
        os.makedirs(out, exist_ok=True)
        for name, img in rendered:
            path = os.path.join(out, name)
            img.save(path)
            print(path)
            written.append(path)

    if args.open and written:
        opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
        # Open each basename once (first output dir only).
        seen = set()
        for path in written:
            base = os.path.basename(path)
            if base in seen:
                continue
            seen.add(base)
            subprocess.call([opener, path])


if __name__ == '__main__':
    main()
