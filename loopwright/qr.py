"""A small QR code writer, so a link printed in the terminal can be scanned by a phone.

Standard library only, and only what a link needs: byte mode, error correction
level L, versions 1 to 10 (up to 271 characters).

    from loopwright.qr import render
    print(render("https://example.com"))
"""

from __future__ import annotations

# version: (data codewords, error-correction codewords per block, blocks), at level L
SPEC = {1: (19, 7, 1), 2: (34, 10, 1), 3: (55, 15, 1), 4: (80, 20, 1), 5: (108, 26, 1),
        6: (136, 18, 2), 7: (156, 20, 2), 8: (194, 24, 2), 9: (232, 30, 2), 10: (274, 18, 4)}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
         7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

# arithmetic in GF(256), the field Reed-Solomon codes are computed in
EXP = [0] * 512
LOG = [0] * 256
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def _ec(data: list[int], count: int) -> list[int]:
    """The Reed-Solomon error-correction codewords for one block."""
    gen = [1]
    for i in range(count):
        gen = [a ^ b for a, b in zip(gen + [0], [0] + [_mul(g, EXP[i]) for g in gen], strict=True)]
    # built as the product of (1 + a^i x), so read left to right it is already highest power first
    rem = [0] * count
    for byte in data:
        factor = byte ^ rem[0]
        rem = rem[1:] + [0]
        for i in range(count):
            rem[i] ^= _mul(gen[i + 1], factor)
    return rem


def _codewords(payload: bytes) -> tuple[int, list[int]]:
    """Pick the smallest version that fits, and lay out data and error correction."""
    for version, spec in SPEC.items():
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * len(payload) <= spec[0] * 8:
            break
    else:
        raise ValueError("too long for a QR code this writer can make")
    capacity, ec_len, blocks = spec
    bits = [0, 1, 0, 0]                                           # byte mode
    bits += [(len(payload) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in payload:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    bits += [0] * min(4, capacity * 8 - len(bits))               # terminator
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    data += [pad[i % 2] for i in range(capacity - len(data))]

    short, extra = divmod(capacity, blocks)
    chunks, at = [], 0
    for b in range(blocks):
        size = short + (1 if b >= blocks - extra else 0)          # the longer blocks come last
        chunks.append(data[at:at + size])
        at += size
    ecs = [_ec(chunk, ec_len) for chunk in chunks]
    out = [chunk[i] for i in range(short + 1) for chunk in chunks if i < len(chunk)]
    out += [ec[i] for i in range(ec_len) for ec in ecs]
    return version, out


def _bch(value: int, bits: int, poly: int) -> int:
    rem = value
    for _ in range(bits):
        rem = (rem << 1) ^ ((rem >> (bits - 1)) * poly)
    return (value << bits) | rem


MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]
FINDER_LIKE = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])


def _penalty(grid: list[list[int]]) -> int:
    """How hard this pattern is for a camera to read. Lower is better."""
    size = len(grid)
    score = 0
    lines = grid + [list(col) for col in zip(*grid, strict=True)]
    for line in lines:
        run = 1
        for i in range(1, size + 1):
            if i < size and line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        for i in range(size - 10):
            if line[i:i + 11] in FINDER_LIKE:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            if grid[y][x] == grid[y][x + 1] == grid[y + 1][x] == grid[y + 1][x + 1]:
                score += 3
    dark = sum(map(sum, grid))
    total = size * size
    score += ((abs(dark * 20 - total * 10) + total - 1) // total - 1) * 10
    return score


def matrix(text: str) -> list[list[int]]:
    """The QR code for `text` as rows of 1 (dark) and 0 (light), without the quiet border."""
    version, codewords = _codewords(text.encode("utf-8"))
    size = 17 + 4 * version
    grid = [[0] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]                 # modules that are not data

    def put(x: int, y: int, dark: int | bool) -> None:
        grid[y][x] = int(bool(dark))
        fixed[y][x] = True

    for i in range(size):                                         # timing lines
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):         # the three corner squares, with their borders
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size:
                    put(x, y, max(abs(dx), abs(dy)) not in (2, 4))
    spots = ALIGN[version]
    for cy in spots:                                              # the small alignment squares
        for cx in spots:
            if (cx, cy) in ((6, 6), (6, size - 7), (size - 7, 6)):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)

    def format_bits(mask: int) -> None:
        bits = _bch((1 << 3) | mask, 10, 0x537) ^ 0x5412          # level L is the bit pair 01
        bit = lambda i: (bits >> i) & 1                            # noqa: E731
        for i in range(6):
            put(8, i, bit(i))
        put(8, 7, bit(6)); put(8, 8, bit(7)); put(7, 8, bit(8))   # noqa: E702
        for i in range(9, 15):
            put(14 - i, 8, bit(i))
        for i in range(8):
            put(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            put(8, size - 15 + i, bit(i))
        put(8, size - 8, 1)

    format_bits(0)                                                # reserves the area; the real bits go in below
    if version >= 7:
        bits = _bch(version, 12, 0x1F25)
        for i in range(18):
            a, b = size - 11 + i % 3, i // 3
            put(a, b, (bits >> i) & 1)
            put(b, a, (bits >> i) & 1)

    stream = [(byte >> i) & 1 for byte in codewords for i in range(7, -1, -1)]
    at = 0
    for right in range(size - 1, 0, -2):                          # zigzag up and down, two columns at a time
        if right <= 6:
            right -= 1
        upward = (right + 1) & 2 == 0
        for step in range(size):
            y = size - 1 - step if upward else step
            for x in (right, right - 1):
                if not fixed[y][x] and at < len(stream):
                    grid[y][x] = stream[at]
                    at += 1

    best: tuple[int, list[list[int]]] | None = None
    for number, mask in enumerate(MASKS):
        format_bits(number)
        tried = [[grid[y][x] ^ int(not fixed[y][x] and mask(x, y)) for x in range(size)] for y in range(size)]
        score = _penalty(tried)
        if best is None or score < best[0]:
            best = (score, tried)
    return best[1]


def render(text: str, border: int = 3, colour: bool = True) -> str:
    """The code drawn with half-height blocks: two rows of the code per line of text.

    With colour it is always dark on white, whatever the terminal's theme."""
    grid = matrix(text)
    size = len(grid) + 2 * border
    rows = [[0] * size for _ in range(border)] + [[0] * border + row + [0] * border for row in grid] + [[0] * size for _ in range(border)]
    if len(rows) % 2:
        rows.append([0] * size)
    glyph = {(0, 0): " ", (1, 0): "▀", (0, 1): "▄", (1, 1): "█"}
    on, off = ("\033[30;107m", "\033[0m") if colour else ("", "")
    return "\n".join(on + "".join(glyph[top, bottom] for top, bottom in zip(rows[i], rows[i + 1], strict=True)) + off for i in range(0, len(rows), 2))
