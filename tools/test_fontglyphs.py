"""fontglyphs 단위 테스트. 시험 폰트는 FontBuilder로 만듦."""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables.TupleVariation import TupleVariation
    from fontTools.ttLib.ttCollection import TTCollection
    import shapely.errors
    from shapely.geometry import MultiLineString, Point as ShapelyPoint, Polygon, box

    import fontglyphs as fg
    import fontsrc
except ImportError as exc:
    raise unittest.SkipTest(f"폰트 의존 없음(tools/requirements.txt): {exc}")

UPM = 1000
ADVANCE = 800
# char_height 10 → 1 단위 = 0.01 mm.
CHAR_HEIGHT = 10.0
LINE_WIDTH = 1.0
CIRCLE_K = 0.5523


def rect(pen, x0: float, y0: float, x1: float, y1: float, ccw: bool = False) -> None:
    pts = [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
    if ccw:
        pts.reverse()
    pen.moveTo(pts[0])
    for p in pts[1:]:
        pen.lineTo(p)
    pen.closePath()


def quad_circle(pen, cx: float, cy: float, r: float) -> None:
    """모든 점이 곡선 밖 제어점인 8분할 원."""
    far = r / math.cos(math.pi / 8)
    offs = [(cx + far * math.cos(math.pi / 8 * (2 * i + 1)), cy + far * math.sin(math.pi / 8 * (2 * i + 1)))
            for i in reversed(range(8))]
    pen.qCurveTo(*offs, None)
    pen.closePath()


def cubic_circle(pen, cx: float, cy: float, r: float) -> None:
    k = CIRCLE_K * r
    pen.moveTo((cx + r, cy))
    pen.curveTo((cx + r, cy - k), (cx + k, cy - r), (cx, cy - r))
    pen.curveTo((cx - k, cy - r), (cx - r, cy - k), (cx - r, cy))
    pen.curveTo((cx - r, cy + k), (cx - k, cy + r), (cx, cy + r))
    pen.curveTo((cx + k, cy + r), (cx + r, cy + k), (cx + r, cy))
    pen.closePath()


Draw = Callable[[object], None]
GLYPHS: dict[str, Draw] = {
    "A": lambda p: rect(p, 100, 0, 700, 600),
    "I": lambda p: rect(p, 100, 0, 150, 600),
    "T": lambda p: (rect(p, 100, 0, 700, 600), rect(p, 800, 0, 830, 600)),
    "O": lambda p: (rect(p, 100, 0, 700, 600), rect(p, 300, 200, 500, 400, ccw=True)),
    "X": lambda p: (rect(p, 100, 0, 500, 400), rect(p, 300, 200, 700, 600)),
    "C": lambda p: quad_circle(p, 400, 400, 300),
    "J": lambda p: rect(p, 100, -200, 400, 700),
    # 폴백 시험용. 선 폭 1 mm 기준 두께 0.7(굵히기), 0.2(중심선), 0.1 점, 작은 구멍의 두께 0.7 고리.
    "E": lambda p: rect(p, 100, 0, 170, 600),
    "S": lambda p: rect(p, 100, 0, 120, 600),
    "P": lambda p: rect(p, 100, 0, 110, 10),
    "Q": lambda p: (rect(p, 100, 0, 270, 170), rect(p, 170, 70, 200, 100, ccw=True)),
    # 면적이 선 폭² 미만인 따로 떨어진 가는 획(0.2×3 mm).
    "K": lambda p: (rect(p, 100, 0, 700, 600), rect(p, 750, 0, 770, 300)),
    # 연결 구조 시험용. 두께 0.7 기둥 사이 1.3×0.2 가로획: 굵혀도 가로획만 빠짐(면적은 선 폭² 미만).
    "H": lambda p: (rect(p, 100, 0, 170, 600), rect(p, 300, 0, 370, 600), rect(p, 160, 290, 310, 310)),
    # 두께 0.2 꺾임(ㄱ), T자 접합(ㅏ).
    "giyeok": lambda p: (rect(p, 100, 550, 500, 570), rect(p, 480, 100, 500, 570)),
    "a_ko": lambda p: (rect(p, 200, 0, 220, 600), rect(p, 210, 290, 400, 310)),
    # 상대 기준 시험용. 굵혀도 선 폭 미만인 가로획은 조각마다 선 폭² 미만이라 절대 기준만으로는 빠져도 통과함.
    # e: 선 폭보다 굵은 세로획(1.2×4)에 0.45×1.2 가로획 셋. t: 선 폭 바로 아래 세로획(0.97)에 양쪽 0.4×1.8 가로획.
    "e_bar": lambda p: (rect(p, 100, 0, 220, 400), rect(p, 170, 0, 340, 45), rect(p, 170, 178, 340, 223),
                        rect(p, 170, 355, 340, 400)),
    "t_bar": lambda p: (rect(p, 300, 0, 397, 570), rect(p, 120, 560, 577, 600)),
}
CHARS = {"A": "A", "I": "I", "T": "T", "O": "O", "X": "X", "C": "C", "J": "J", "E": "E", "S": "S", "P": "P",
         "Q": "Q", "K": "K", "H": "H", "giyeok": "ㄱ", "a_ko": "ㅏ",
         "e_bar": "e", "t_bar": "t", "space": " "}
# 가변 폰트: wght 900 에서 I 오른쪽 변이 100 단위 늘어남.
WGHT_AXIS = ("wght", 100, 400, 900, "Weight")
VAR_WIDEN = 100


def build_ttf(path: Path) -> None:
    fb = FontBuilder(UPM, isTTF=True)
    order = [".notdef", *CHARS]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(ch): name for name, ch in CHARS.items()})
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name in GLYPHS:
            GLYPHS[name](pen)
        glyphs[name] = pen.glyph()
    fb.setupGlyf(glyphs)
    # glyf 그리기는 hmtx lsb와 xMin 차이만큼 옮기므로 lsb를 xMin에 맞춤.
    glyf = fb.font["glyf"]
    fb.setupHorizontalMetrics({name: (250 if name == "space" else ADVANCE, getattr(glyf[name], "xMin", 0))
                               for name in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


def build_var(path: Path) -> None:
    fb = FontBuilder(UPM, isTTF=True)
    order = [".notdef", "I"]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord("I"): "I"})
    glyphs = {name: TTGlyphPen(None).glyph() for name in order}
    pen = TTGlyphPen(None)
    GLYPHS["I"](pen)
    glyphs["I"] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({".notdef": (ADVANCE, 0), "I": (ADVANCE, 100)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestVar", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.setupFvar([WGHT_AXIS], [])
    # rect 점 순서 (x0,y0),(x0,y1),(x1,y1),(x1,y0) 뒤에 위상 점 4개.
    deltas = [(0, 0), (0, 0), (VAR_WIDEN, 0), (VAR_WIDEN, 0), (0, 0), (0, 0), (0, 0), (0, 0)]
    fb.setupGvar({"I": [TupleVariation({"wght": (0, 1.0, 1.0)}, deltas)]})
    fb.save(str(path))


def build_otf(path: Path) -> None:
    fb = FontBuilder(UPM, isTTF=False)
    order = [".notdef", "C"]
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord("C"): "C"})
    strings = {}
    for name in order:
        pen = T2CharStringPen(ADVANCE, None)
        if name == "C":
            cubic_circle(pen, 400, 400, 300)
        strings[name] = pen.getCharString()
    fb.setupCFF("TestCFF", {"FullName": "TestCFF"}, strings, {})
    fb.setupHorizontalMetrics({name: (ADVANCE, 0) for name in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "TestCFF", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


def points(strokes: list[fg.Stroke]) -> list[fg.Point]:
    return [p for s in strokes for p in s]


def bounds(stroke: fg.Stroke) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in stroke], [p[1] for p in stroke]
    return min(xs), min(ys), max(xs), max(ys)


class FontTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        build_ttf(cls.dir / "t.ttf")
        build_otf(cls.dir / "t.otf")
        build_var(cls.dir / "v.ttf")
        cls.font = fg.OutlineFont(cls.dir / "t.ttf")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def paths(self, ch: str, style: str = "fill", font: fg.OutlineFont | None = None,
              fallback: str = "none") -> fg.GlyphResult:
        return (font or self.font).glyph_paths(ch, CHAR_HEIGHT, LINE_WIDTH, style, fallback)


class TraceRegionTest(unittest.TestCase):
    """글리프가 아닌 영역도 같은 경로 변환을 씀. 글자 전용 값은 채우지 않음."""

    def test_fills_region(self) -> None:
        result = fg.trace_region(box(0, 0, 40, 20), 40.0, LINE_WIDTH)
        self.assertEqual((result.method, result.too_thin, result.advance, result.min_char_height),
                         ("fill", False, 0.0, None))
        self.assertTrue(result.strokes)

    def test_thin_region_flagged(self) -> None:
        result = fg.trace_region(box(0, 0, 40, 1), 40.0, LINE_WIDTH, fallback="none")
        self.assertTrue(result.too_thin)

    def test_fallback_stage(self) -> None:
        result = fg.trace_region(box(0, 0, 40, LINE_WIDTH * 0.8), 40.0, LINE_WIDTH, fallback="embolden")
        self.assertEqual(result.method, "embolden")


class FillTest(FontTest):
    def test_square_rings(self) -> None:
        r = self.paths("A")
        # 6×6 mm, 선 폭 1: 안쪽으로 0.5, 1.5, 2.5 mm.
        self.assertEqual(len(r.strokes), 3)
        self.assertFalse(r.too_thin)
        self.assertAlmostEqual(r.advance, 8.0)
        for ring in r.strokes:
            self.assertEqual(ring[0], ring[-1])
        widths = [bounds(s)[2] - bounds(s)[0] for s in r.strokes]
        for outer, inner in zip(widths, widths[1:]):
            self.assertAlmostEqual(outer - inner, 2 * LINE_WIDTH, places=3)
        self.assertAlmostEqual(widths[0], 5.0, places=3)

    def test_outline_only_first(self) -> None:
        r = self.paths("A", "outline")
        self.assertEqual(len(r.strokes), 1)
        self.assertAlmostEqual(bounds(r.strokes[0])[0], 1.5, places=3)

    def test_thin(self) -> None:
        r = self.paths("I")
        self.assertTrue(r.too_thin)
        self.assertEqual(r.strokes, [])

    def test_partly_thin(self) -> None:
        r = self.paths("T", "outline")
        self.assertTrue(r.too_thin)
        self.assertEqual(len(r.strokes), 1)

    def test_hole(self) -> None:
        r = self.paths("O", "outline")
        self.assertEqual(len(r.strokes), 2)
        outer, inner = sorted(r.strokes, key=lambda s: Polygon(s).area, reverse=True)
        self.assertAlmostEqual(bounds(outer)[0], 1.5, places=3)
        self.assertAlmostEqual(bounds(inner)[0], 2.5, places=3)
        self.assertFalse(Polygon(outer, [inner]).contains(ShapelyPoint(4, 3)))

    def test_overlap_nonzero(self) -> None:
        # 같은 방향으로 겹친 윤곽은 구멍이 아님(TrueType, CFF 렌더러와 같음).
        r = self.paths("X", "outline")
        self.assertEqual(len(r.strokes), 1)

    def test_baseline_y_up(self) -> None:
        ys = [p[1] for p in points(self.paths("A", "outline").strokes)]
        self.assertAlmostEqual(min(ys), 0.5, places=3)
        self.assertAlmostEqual(max(ys), 5.5, places=3)
        self.assertAlmostEqual(min(p[1] for p in points(self.paths("J", "outline").strokes)), -1.5, places=3)


class CurveTest(FontTest):
    def check_circle(self, r: fg.GlyphResult) -> None:
        self.assertEqual(len(r.strokes), 1)
        expected = 3.0 - LINE_WIDTH / 2
        for x, y in r.strokes[0]:
            self.assertAlmostEqual(math.hypot(x - 4, y - 4), expected, delta=fg.FLATTEN_TOLERANCE_MM + 0.02)

    def test_quadratic(self) -> None:
        self.check_circle(self.paths("C", "outline"))

    def test_cubic(self) -> None:
        self.check_circle(self.paths("C", "outline", fg.OutlineFont(self.dir / "t.otf")))


class SpecialTest(FontTest):
    def test_space(self) -> None:
        r = self.paths(" ")
        self.assertEqual((r.strokes, r.advance, r.too_thin), ([], 2.5, False))
        self.assertEqual((r.method, r.min_char_height), ("none", None))
        self.assertAlmostEqual(self.paths("　").advance, fg.DEFAULT_SPACE_EM * CHAR_HEIGHT)

    def test_missing(self) -> None:
        self.assertFalse(self.font.has_glyph("가"))
        self.assertTrue(self.font.has_glyph("A"))
        with self.assertRaisesRegex(fg.FontGlyphError, "없는 글자"):
            self.paths("가")

    def test_bad_args(self) -> None:
        with self.assertRaisesRegex(fg.FontGlyphError, "스타일"):
            self.paths("A", "bold")
        with self.assertRaises(fg.FontGlyphError):
            self.font.glyph_paths("A", 0, LINE_WIDTH)
        with self.assertRaisesRegex(fg.FontGlyphError, "폴백"):
            self.paths("A", fallback="bold")

    def test_cache(self) -> None:
        first = self.paths("O")
        first.strokes.clear()
        self.assertTrue(self.paths("O").strokes)
        self.assertIn(("O", CHAR_HEIGHT, LINE_WIDTH, "fill", "none"), self.font._cache)
        self.assertEqual(self.paths("E", fallback="auto").method, "embolden")
        self.assertTrue(self.paths("E").too_thin)

    def test_collection_rejected(self) -> None:
        coll = TTCollection()
        coll.fonts = [TTFont(self.dir / "t.ttf"), TTFont(self.dir / "t.ttf")]
        coll.save(self.dir / "t.ttc")
        with self.assertRaisesRegex(fg.FontGlyphError, "컬렉션"):
            fg.OutlineFont(self.dir / "t.ttc")

    def test_not_font(self) -> None:
        bad = self.dir / "bad.ttf"
        bad.write_bytes(b"not a font")
        with self.assertRaisesRegex(fg.FontGlyphError, "읽기 실패"):
            fg.OutlineFont(bad)


class DefenseTest(FontTest):
    def test_units_per_em_range(self) -> None:
        for upm in (8, 20000):
            font = TTFont(self.dir / "t.ttf")
            font["head"].unitsPerEm = upm
            path = self.dir / f"upm{upm}.ttf"
            font.save(path)
            with self.subTest(upm=upm), self.assertRaisesRegex(fg.FontGlyphError, "unitsPerEm"):
                fg.OutlineFont(path)

    def test_point_limit(self) -> None:
        with mock.patch.object(fg, "MAX_GLYPH_POINTS", 3), self.assertRaisesRegex(fg.FontGlyphError, "점이"):
            fg.OutlineFont(self.dir / "t.ttf").glyph_paths("A", CHAR_HEIGHT, LINE_WIDTH)

    def test_contour_limit(self) -> None:
        with mock.patch.object(fg, "MAX_GLYPH_CONTOURS", 1), self.assertRaisesRegex(fg.FontGlyphError, "윤곽이"):
            fg.OutlineFont(self.dir / "t.ttf").glyph_paths("O", CHAR_HEIGHT, LINE_WIDTH)

    def test_corrupt_glyph(self) -> None:
        # 윤곽 수를 부풀리면 fontTools 가 struct.error 를 냄.
        font = TTFont(self.dir / "t.ttf")
        offset = font.reader.tables["glyf"].offset + font["loca"][font.getGlyphID("A")]
        data = bytearray((self.dir / "t.ttf").read_bytes())
        struct.pack_into(">h", data, offset, 30000)
        path = self.dir / "corrupt.ttf"
        path.write_bytes(bytes(data))
        with self.assertRaisesRegex(fg.FontGlyphError, "글리프 읽기 실패"):
            fg.OutlineFont(path).glyph_paths("A", CHAR_HEIGHT, LINE_WIDTH)

    def test_bad_metrics(self) -> None:
        font = fg.OutlineFont(self.dir / "t.ttf")
        font._hmtx = {}
        with self.assertRaisesRegex(fg.FontGlyphError, "글자 폭 읽기 실패"):
            font.glyph_paths("A", CHAR_HEIGHT, LINE_WIDTH)

    def test_geometry_error(self) -> None:
        font = fg.OutlineFont(self.dir / "t.ttf")
        with mock.patch.object(fg, "_fill", side_effect=shapely.errors.GEOSException("bad")), \
                self.assertRaisesRegex(fg.FontGlyphError, "처리 실패"):
            font.glyph_paths("A", CHAR_HEIGHT, LINE_WIDTH)


class FallbackTest(FontTest):
    def auto(self, ch: str, style: str = "fill") -> fg.GlyphResult:
        return self.paths(ch, style, fallback="auto")

    def test_normal_untouched(self) -> None:
        r = self.auto("A")
        self.assertEqual((r.method, r.too_thin), ("fill", False))
        self.assertEqual(r.strokes, self.paths("A").strokes)
        self.assertEqual(self.auto("A", "outline").method, "outline")

    def test_embolden(self) -> None:
        self.assertTrue(self.paths("E").too_thin)
        r = self.auto("E")
        self.assertEqual((r.method, r.too_thin), ("embolden", False))
        self.assertTrue(r.strokes)
        # 굵힌 거리는 상한 안. 0.7 → 1.0 mm 이상이면 됨.
        x0, _, x1, _ = bounds(r.strokes[0])
        width = x1 - x0 + LINE_WIDTH
        self.assertGreaterEqual(width, LINE_WIDTH - 1e-6)
        self.assertLessEqual(width, 0.7 + 2 * fg.EMBOLDEN_MAX_LINE_WIDTHS * LINE_WIDTH + 1e-6)

    def test_center(self) -> None:
        self.assertIsNone(self.paths("S", fallback="embolden").strokes or None)
        r = self.auto("S")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        self.assertEqual(len(r.strokes), 1)
        line = r.strokes[0]
        self.assertGreater(fg._length(line), 5.0)
        for x, _ in line:
            self.assertAlmostEqual(x, 1.1, delta=0.11)

    def test_dot_too_thin(self) -> None:
        r = self.auto("P")
        self.assertEqual((r.strokes, r.too_thin), ([], True))

    def test_hole_blocks_embolden(self) -> None:
        # 두께 0.7 은 굵히면 채워지지만 0.3 구멍이 막히므로 중심선으로 넘어감.
        self.assertTrue(self.paths("Q", fallback="embolden").too_thin)
        r = self.auto("Q")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        self.assertEqual(len(r.strokes), 1)
        self.assertEqual(r.strokes[0][0], r.strokes[0][-1])

    def test_partly_thin_keeps_rings(self) -> None:
        # 굵은 사각형 고리는 그대로 두고 0.3 mm 막대에만 중심선을 더함.
        rings = self.paths("T").strokes
        r = self.auto("T")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        self.assertEqual(r.strokes[:len(rings)], rings)
        extra = r.strokes[len(rings):]
        self.assertEqual(len(extra), 1)
        self.assertTrue(all(8.0 <= x <= 8.3 for x, _ in extra[0]))

    def test_small_detached_stroke(self) -> None:
        # 면적은 작아도 획이 통째로 빠지면 굵기 부족이고 중심선으로 살림.
        self.assertTrue(self.paths("K").too_thin)
        r = self.auto("K")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        self.assertTrue(any(all(7.4 <= x <= 7.8 for x, _ in s) for s in r.strokes))

    def test_single_stage(self) -> None:
        self.assertEqual(self.paths("S", fallback="center").method, "center")
        r = self.paths("S", fallback="embolden")
        self.assertEqual((r.method, r.too_thin), ("fill", True))
        self.assertEqual(self.paths("E", fallback="center").method, "center")


# 선 자국 경계에 생기는 이보다 작은 자투리는 조각으로 세지 않음.
SLIVER_LINE_WIDTHS_SQ = 0.01


def region_of(font: fg.OutlineFont, ch: str, char_height: float) -> Polygon:
    return font._draw(ch, font._cmap[ord(ch)], char_height / font._units_per_em, fg.FLATTEN_TOLERANCE_MM)


def check_structure(test: unittest.TestCase, font: fg.OutlineFont, ch: str, r: fg.GlyphResult,
                    char_height: float, line_width: float) -> None:
    """선 자국이 원 영역 조각마다 하나로 이어지고 영역을 거의 다 덮음."""
    region = region_of(font, ch, char_height)
    ink = MultiLineString([s for s in r.strokes if len(s) > 1]).buffer(line_width / 2)
    for poly in fg._polygons(region):
        parts = [p for p in fg._polygons(ink.intersection(poly)) if p.area > SLIVER_LINE_WIDTHS_SQ * line_width ** 2]
        test.assertEqual(len(parts), 1, ch)
    test.assertGreater(ink.intersection(region).area / region.area, 0.95, ch)


class StructureTest(FontTest):
    def auto(self, ch: str) -> fg.GlyphResult:
        return self.paths(ch, fallback="auto")

    def test_crossbar_not_silently_lost(self) -> None:
        # 이전 판정은 빠진 가로획 면적이 선 폭² 미만이라 굵히기를 통과시켰음.
        r = self.auto("H")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        check_structure(self, self.font, "H", r, CHAR_HEIGHT, LINE_WIDTH)

    def test_judge_broken(self) -> None:
        region = region_of(self.font, "H", CHAR_HEIGHT)
        judge = fg._Judge(region, CHAR_HEIGHT, LINE_WIDTH)
        # 빠진 가로획 1.3×0.2 는 면적 기준만으로는 통과함.
        self.assertTrue(judge.too_thin(region.difference(box(1.7, 0, 3.0, 6))))
        self.assertFalse(judge.too_thin(region))

    def test_judge_hole(self) -> None:
        # 고리 한쪽이 빠져 구멍을 못 둘러싸면 하나로 이어져 있어도 부족.
        region = region_of(self.font, "Q", CHAR_HEIGHT)
        judge = fg._Judge(region, CHAR_HEIGHT, LINE_WIDTH)
        self.assertTrue(judge.too_thin(region.difference(box(2.0, 0.75, 2.7, 0.95))))
        self.assertFalse(judge.too_thin(region))

    def test_bend(self) -> None:
        r = self.auto("ㄱ")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        self.assertEqual(len(r.strokes), 1)
        self.assertGreater(fg._length(r.strokes[0]), 8.0)
        check_structure(self, self.font, "ㄱ", r, CHAR_HEIGHT, LINE_WIDTH)

    def test_t_junction(self) -> None:
        r = self.auto("ㅏ")
        self.assertEqual((r.method, r.too_thin), ("center", False))
        self.assertLessEqual(len(r.strokes), 2)
        self.assertGreater(sum(fg._length(s) for s in r.strokes), 7.0)
        check_structure(self, self.font, "ㅏ", r, CHAR_HEIGHT, LINE_WIDTH)

    def test_center_gap_reported(self) -> None:
        # 중심선이 가로획을 빠뜨리면 조용히 통과하지 않고 굵기 부족으로 보고함.
        whole = fg._Judge.skeleton.fget

        def cut(judge: fg._Judge) -> object:
            return whole(judge).difference(box(1.5, 2.7, 3.2, 3.2))

        font = fg.OutlineFont(self.dir / "t.ttf")
        with mock.patch.object(fg._Judge, "skeleton", property(cut)):
            r = font.glyph_paths("H", CHAR_HEIGHT, LINE_WIDTH, fallback="center")
        self.assertTrue(r.too_thin)


class RelativeLossTest(FontTest):
    """선 폭 기준 조각은 작아도 원 영역 대비 많이 빠지면 굵기 부족."""

    def absolute_only(self, ch: str) -> fg.GlyphResult:
        font = fg.OutlineFont(self.dir / "t.ttf")
        with mock.patch.multiple(fg, THIN_LOST_TOTAL_RATIO=1.0, THIN_LOST_PIECE_RATIO=1.0):
            return font.glyph_paths(ch, CHAR_HEIGHT, LINE_WIDTH)

    def test_absolute_only_loses_bars(self) -> None:
        # 상대 기준이 없던 판정: e 는 fill, t 는 embolden 으로 통과하지만 가로획이 빠짐.
        for ch, method in (("e", "fill"), ("t", "embolden")):
            r = self.absolute_only(ch)
            self.assertEqual((r.method, r.too_thin), (method, False), ch)
            ink = MultiLineString([s for s in r.strokes if len(s) > 1]).buffer(LINE_WIDTH / 2)
            region = region_of(self.font, ch, CHAR_HEIGHT)
            self.assertLess(ink.intersection(region).area / region.area, 0.9, ch)

    def test_bars_kept(self) -> None:
        for ch in "et":
            r = self.paths(ch, fallback="auto")
            self.assertEqual((r.method, r.too_thin), ("center", False), ch)
            check_structure(self, self.font, ch, r, CHAR_HEIGHT, LINE_WIDTH)

    def test_judge_total(self) -> None:
        region = region_of(self.font, "e", CHAR_HEIGHT)
        judge = fg._Judge(region, CHAR_HEIGHT, LINE_WIDTH)
        stem = region.intersection(box(0, 0, 2.2, 6))
        # 가로획 셋 0.54 mm² 씩은 조각 기준(선 폭² 1 mm²) 안이지만 총 25% 가 빠짐.
        self.assertGreater(region.difference(stem).area, fg.THIN_LOST_TOTAL_RATIO * region.area)
        self.assertTrue(judge.too_thin(stem))
        self.assertFalse(judge.too_thin(region))

    def test_judge_piece(self) -> None:
        region = region_of(self.font, "t", CHAR_HEIGHT)
        judge = fg._Judge(region, CHAR_HEIGHT, LINE_WIDTH)
        # 원 영역 약 7.3 mm² 라 조각 상한은 선 폭²보다 작은 원 영역 비율 쪽.
        self.assertAlmostEqual(judge.piece_limit, fg.THIN_LOST_PIECE_RATIO * region.area)
        self.assertLess(judge.piece_limit, LINE_WIDTH ** 2)
        # 왼쪽 가로획 0.72 mm² 는 선 폭² 미만이지만 상한을 넘음. 그 절반은 통과.
        self.assertTrue(judge.too_thin(region.difference(box(1.2, 5.6, 3.0, 6.0))))
        self.assertFalse(judge.too_thin(region.difference(box(1.2, 5.6, 1.9, 6.0))))


class MinHeightTest(FontTest):
    def test_min_height(self) -> None:
        for ch in "AIETCOet":
            h = self.paths(ch).min_char_height
            self.assertIsNotNone(h, ch)
            self.assertFalse(self.font.glyph_paths(ch, h, LINE_WIDTH, fallback="none").too_thin, ch)
            self.assertTrue(self.font.glyph_paths(ch, 0.9 * h, LINE_WIDTH, fallback="none").too_thin, ch)

    def test_scales_with_line_width(self) -> None:
        # 두께 0.05 em: 선 폭 1 mm 이면 약 20 mm.
        h = self.paths("I").min_char_height
        self.assertAlmostEqual(h, fg.MIN_HEIGHT_MARGIN * LINE_WIDTH / 0.05, delta=0.5)
        self.assertAlmostEqual(self.font.glyph_paths("I", CHAR_HEIGHT, 2 * LINE_WIDTH).min_char_height, 2 * h)


class WeightTest(FontTest):
    def test_instance(self) -> None:
        light = fg.OutlineFont(self.dir / "v.ttf")
        bold = fg.OutlineFont(self.dir / "v.ttf", weight=900)
        self.assertTrue(self.paths("I", font=light).too_thin)
        r = self.paths("I", font=bold)
        self.assertFalse(r.too_thin)
        self.assertAlmostEqual(bounds(r.strokes[0])[2], 2.5 - LINE_WIDTH / 2, places=3)
        # 400–900 사이 선형 보간: 775 에서 폭 0.5 + 0.75 mm.
        mid = fg.OutlineFont(self.dir / "v.ttf", weight=775)
        self.assertAlmostEqual(bounds(self.paths("I", "outline", mid).strokes[0])[2], 1.75, places=3)
        self.assertEqual(bold.weight, 900)
        self.assertIn(("I", CHAR_HEIGHT, LINE_WIDTH, "fill", "none"), bold._cache)

    def test_out_of_range(self) -> None:
        with self.assertRaisesRegex(fg.FontGlyphError, "100–900"):
            fg.OutlineFont(self.dir / "v.ttf", weight=1000)

    def test_static(self) -> None:
        with self.assertRaisesRegex(fg.FontGlyphError, "wght"):
            fg.OutlineFont(self.dir / "t.ttf", weight=700)


SYSTEM_KOREAN_FONTS = [
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
]
SYSTEM_KOREAN = next((p for p in SYSTEM_KOREAN_FONTS if p.is_file()), None)


def cached_font(family: str, name: str) -> Path | None:
    """fontsrc 캐시에 받아 둔 폰트. 없으면 None."""
    try:
        path = fontsrc.cache_root() / fontsrc.slugify(family) / name
    except fontsrc.FontError:
        return None
    return path if path.is_file() else None


BLACK_HAN_SANS = cached_font("Black Han Sans", "BlackHanSans-Regular.ttf")
# 글자 하나의 선 수 상한. 넘으면 조각난 것으로 봄.
MAX_STROKES_PER_GLYPH = 10


@unittest.skipUnless(SYSTEM_KOREAN, "시스템 한글 폰트 없음")
class SystemKoreanTest(unittest.TestCase):
    def test_hangul(self) -> None:
        font = fg.OutlineFont(SYSTEM_KOREAN)
        r = font.glyph_paths("생", 30, 1.0)
        self.assertTrue(r.strokes)
        self.assertGreater(r.advance, 0)
        self.assertGreaterEqual(len(font.glyph_paths("ㅇ", 30, 1.0, "outline").strokes), 2)

    def test_thick_line_structure(self) -> None:
        # 선 폭이 글자에 비해 굵을 때 폴백 결과가 끊긴 조각으로 통과하던 문제.
        font = fg.OutlineFont(SYSTEM_KOREAN)
        line_width = 4.0
        for text, heights in (("HAPPY", (20, 30)), ("생일축하해", (30, 40))):
            for height in heights:
                for ch in text:
                    if not font.has_glyph(ch):
                        continue
                    with self.subTest(ch=ch, height=height):
                        r = font.glyph_paths(ch, height, line_width)
                        self.assertFalse(r.too_thin)
                        self.assertLessEqual(len(r.strokes), MAX_STROKES_PER_GLYPH)
                        check_structure(self, font, ch, r, height, line_width)


@unittest.skipUnless(BLACK_HAN_SANS, "Black Han Sans 캐시 없음")
class BlackHanSansTest(unittest.TestCase):
    def test_crossbars_not_lost(self) -> None:
        # 선 폭 4 mm, 16.22 mm 에서 절대 기준만으로는 E 는 fill 로 48%, T 는 embolden 으로 27% 가 빠졌음.
        font = fg.OutlineFont(BLACK_HAN_SANS)
        height, line_width = 16.22, 4.0
        for ch in "ET":
            with self.subTest(ch=ch):
                r = font.glyph_paths(ch, height, line_width)
                if r.too_thin:
                    continue
                region = region_of(font, ch, height)
                ink = MultiLineString([s for s in r.strokes if len(s) > 1]).buffer(line_width / 2)
                self.assertGreater(ink.intersection(region).area / region.area, 0.9)


if __name__ == "__main__":
    unittest.main()
