"""cream_text 단위 테스트. 계약 형식의 임시 프로필과 가짜 폰트로 검증함."""

from __future__ import annotations

import importlib.util
import io
import math
import re
import string
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cream_text as ct  # noqa: E402

PRINTER_CFG = """\
[mcu]
serial: /dev/null

[printer]
kinematics: cartesian
max_velocity: 150
max_accel: 1500  # 주석
max_z_velocity: 10

[stepper_x]
position_min: 0
position_max: 330

[stepper_y]
position_max: 235

[stepper_z]
position_min: -2
position_max: 250

[extruder]
nozzle_diameter: 8.0
filament_diameter: 26.7

[include cream_vars.cfg]
[include ../../klipper/macros/cream.cfg]

#*# <---- SAVE_CONFIG ---->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
#*# [stepper_z]
#*# position_max = 260
"""

CREAM_VARS = """\
[gcode_macro _CREAM_CFG]
variable_swap_x: 0
variable_swap_y: 117.5
variable_swap_z: 80
variable_swap_lift: 15
variable_prime_x: 320
variable_prime_y: 235
variable_prime_z: 3
variable_prime_mm: 0.36
variable_load_mm: 10
variable_print_accel: 800
variable_travel_speed: 100
variable_print_speed: 20
variable_tool_count: 3
variable_center_x: 160
variable_center_y: 117.5
variable_line_width: 4
variable_line_height: 2.5
variable_gap: 3
variable_piston_speed: 5
variable_piston_max: 90
gcode:
"""

MOVE = re.compile(r"^G1 (.*) F\d+$")
PROFILE = ("--profile", "hardware/test")


def make_profile(root: Path, vars_text: str = CREAM_VARS) -> Path:
    profile = root / "hardware" / "test"
    profile.mkdir(parents=True)
    (profile / "printer.cfg").write_text(PRINTER_CFG, encoding="utf-8")
    (profile / "cream_vars.cfg").write_text(vars_text, encoding="utf-8")
    return profile


def make_job(**kw: object) -> ct.Job:
    base = dict(lines=["HAPPY", "BIRTHDAY"], cake_diameter=210, height=150, gap=3, lift=5,
                char_height=None, line_width=4, line_height=2.5, margin=10, border=False,
                border_inset=8, flowers=0, flower_inset=20, flower_ml=0.5)
    base.update(kw)
    return ct.Job(**base)


def moves(gcode: str) -> list[dict[str, float]]:
    """이동마다 누적 위치(X, Y, Z)와 그 줄의 E를 반환함."""
    result = []
    pos: dict[str, float] = {}
    for line in gcode.splitlines():
        m = MOVE.match(line)
        if m:
            words = {w[0]: float(w[1:]) for w in m.group(1).split()}
            pos.update({k: v for k, v in words.items() if k != "E"})
            result.append({**pos, **({"E": words["E"]} if "E" in words else {})})
    return result


def extrude_runs(gcode: str) -> list[list[tuple[float, float]]]:
    """Z를 내린 뒤 이어 짠 구간마다 시작점과 짠 점들."""
    runs, current, last = [], None, None
    for mv in moves(gcode):
        if "E" in mv and "X" in mv:
            if current is None:
                current = [last]
            current.append((mv["X"], mv["Y"]))
        else:
            if current:
                runs.append(current)
            current = None
            if "X" in mv:
                last = (mv["X"], mv["Y"])
    return runs


@dataclass(frozen=True)
class FakeGlyph:
    strokes: list[ct.Stroke]
    advance: float
    too_thin: bool
    method: str
    min_char_height: float | None


def _box(x0: float, y0: float, x1: float, y1: float) -> ct.Stroke:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


class FakeFont:
    """OutlineFont 계약을 따르는 가짜 폰트. em 1 기준 모양을 char_height 배로 키움."""

    # 글자: (방법, 모양, advance, 가늚, 해결 em 높이 mm)
    BOX = ("fill", [_box(0.1, 0, 0.6, 0.7)], 0.7, False, None)
    SHAPES = {
        **dict.fromkeys(string.ascii_uppercase + string.digits + "생", BOX),
        "A": ("fill", [_box(0.1, 0, 0.6, 0.7), _box(0.25, 0.15, 0.45, 0.55)], 0.7, False, None),
        "e": ("embolden", [_box(0.1, 0, 0.4, 0.5)], 0.5, False, None),
        ".": ("center", [[(0.2, 0), (0.2, 0.1)]], 0.4, False, None),
        "i": ("center", [[(0.2, 0), (0.2, 0.5)]], 0.4, True, 40.0),
        "x": ("none", [], 0.4, True, None),
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, float, float, str, str]] = []

    def has_glyph(self, ch: str) -> bool:
        return ch in self.SHAPES

    def glyph_paths(self, ch: str, char_height: float, line_width: float, style: str = "fill",
                    fallback: str = "auto") -> FakeGlyph:
        self.calls.append((ch, char_height, line_width, style, fallback))
        if ch == " ":
            return FakeGlyph([], 0.3 * char_height, False, "none", None)
        method, shape, advance, thin, need = self.SHAPES[ch]
        method = style if method == "fill" else method
        strokes = [[(x * char_height, y * char_height) for x, y in s] for s in shape]
        return FakeGlyph(strokes, advance * char_height, thin, method, need)


FAKE_OPTS = ct.FontOptions(spec="fake")


def build(machine: ct.Machine, job: ct.Job) -> tuple[str, dict[int, float]]:
    return ct.build_gcode(machine, job, ct.font_text(job, FakeFont(), FAKE_OPTS))


class TempProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.profile = make_profile(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *argv: str, factory: ct.FontFactory | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ct.main(list(argv), root=self.root, font_factory=factory or (lambda opts: FakeFont()))
        return code, out.getvalue(), err.getvalue()


class LoadMachineTest(TempProfileTest):
    def test_reads_profile(self) -> None:
        m = ct.load_machine(self.profile)
        self.assertEqual(m.limits["x"], (0.0, 330.0))
        self.assertEqual(m.limits["y"], (0.0, 235.0))
        self.assertEqual(m.limits["z"], (-2.0, 260.0))  # SAVE_CONFIG 우선
        self.assertEqual(m.center, (160.0, 117.5))
        self.assertEqual((m.travel_speed, m.print_speed, m.print_accel), (100.0, 20.0, 800.0))
        self.assertEqual((m.max_accel, m.tool_count), (1500.0, 3))
        self.assertAlmostEqual(m.filament_area, math.pi / 4 * 26.7 ** 2)
        self.assertAlmostEqual(m.max_cross_section, 256.0)
        self.assertEqual((m.line_width, m.line_height, m.gap), (4.0, 2.5, 3.0))

    def test_tool_count_not_integer(self) -> None:
        (self.profile / "cream_vars.cfg").write_text(
            CREAM_VARS.replace("variable_tool_count: 3", "variable_tool_count: 2.5"))
        with self.assertRaisesRegex(ct.CreamTextError, "정수"):
            ct.load_machine(self.profile)

    def test_missing_var(self) -> None:
        (self.profile / "cream_vars.cfg").write_text(CREAM_VARS.replace("variable_print_speed: 20\n", ""))
        with self.assertRaisesRegex(ct.CreamTextError, "print_speed"):
            ct.load_machine(self.profile)

    def test_center_override(self) -> None:
        (self.profile / "cream_vars.cfg").write_text(
            CREAM_VARS.replace("variable_center_x: 160\nvariable_center_y: 117.5\n", ""))
        self.assertEqual(ct.load_machine(self.profile, (150.0, 100.0)).center, (150.0, 100.0))
        with self.assertRaisesRegex(ct.CreamTextError, "center_x"):
            ct.load_machine(self.profile)


class BuildTest(TempProfileTest):
    def setUp(self) -> None:
        super().setUp()
        self.machine = ct.load_machine(self.profile)

    def test_sequence_and_ranges(self) -> None:
        gcode, volumes = build(self.machine, make_job(border=True, flowers=8))
        lines = gcode.splitlines()
        self.assertEqual(lines[1], "G28")
        for tool in ("T0", "T1", "T2"):
            i = lines.index(tool)
            self.assertEqual(lines[i + 1], "PRIME")
        self.assertLess(lines.index("G28"), lines.index("T0"))
        # 매크로가 SAVE/RESTORE_GCODE_STATE로 좌표 모드를 되돌리므로 처음 한 번만 지정함.
        self.assertEqual((lines.count("G90"), lines.count("M83")), (1, 1))
        self.assertNotIn("CREAM_LOAD", lines)
        self.assertEqual(set(volumes), {0, 1, 2})
        self.assertAlmostEqual(volumes[2], 4.0)
        cx, cy = self.machine.center
        for mv in moves(gcode):
            for axis in "XYZ":
                if axis in mv:
                    low, high = self.machine.limits[axis.lower()]
                    self.assertTrue(low <= mv[axis] <= high, mv)
            if "E" in mv:
                self.assertLessEqual(math.hypot(mv["X"] - cx, mv["Y"] - cy), 105)
                self.assertEqual(mv["Z"], 153.0)

    def test_lift_before_xy_after_prime(self) -> None:
        gcode, _ = build(self.machine, make_job())
        lines = gcode.splitlines()
        after = lines[lines.index("PRIME") + 1:]
        first = next(l for l in after if l.startswith("G1"))
        self.assertTrue(first.startswith("G1 Z158.000"), first)

    def test_text_centered(self) -> None:
        gcode, _ = build(self.machine, make_job(lines=["HI"], char_height=20))
        xs = [m["X"] for m in moves(gcode) if "E" in m]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 160.0, places=1)

    def test_text_outside_cake(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "반경 밖"):
            build(self.machine, make_job(lines=["HELLO"], char_height=60))

    def test_char_height_invalid(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "char_height"):
            ct.check_job(self.machine, make_job(char_height=0))
        with self.assertRaisesRegex(ct.CreamTextError, "최소"):
            build(self.machine, make_job(char_height=1))

    def test_inset_beyond_radius(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "flower_inset .* 반경"):
            ct.check_job(self.machine, make_job(flowers=4, flower_inset=120))
        with self.assertRaisesRegex(ct.CreamTextError, "border_inset .* 반경"):
            ct.check_job(self.machine, make_job(border=True, border_inset=105))

    def test_too_long(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "너무 김"):
            build(self.machine, make_job(lines=["ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"], cake_diameter=150))

    def test_height_out_of_range(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "범위 초과: Z"):
            build(self.machine, make_job(height=255))

    def test_cake_outside_axis(self) -> None:
        machine = ct.Machine(**{**self.machine.__dict__, "center": (10.0, 117.5)})
        with self.assertRaisesRegex(ct.CreamTextError, "범위 초과: X"):
            build(machine, make_job(lines=["HI"]))

    def test_tool_count(self) -> None:
        machine = ct.Machine(**{**self.machine.__dict__, "tool_count": 2})
        with self.assertRaisesRegex(ct.CreamTextError, "카트리지 T2 없음"):
            build(machine, make_job(flowers=8))

    def test_cross_section(self) -> None:
        job = make_job(line_width=20, line_height=20)
        with self.assertRaisesRegex(ct.CreamTextError, "max_extrude_cross_section"):
            ct.build_gcode(self.machine, job, ct.TextLayout(job.lines, []))

    def test_header_ascii(self) -> None:
        gcode, _ = build(self.machine, make_job(lines=["A생", "HI"], char_height=20))
        self.assertEqual(gcode.splitlines()[0], "; printc cream_text: A\\uc0dd / HI")
        self.assertTrue(gcode.isascii())


class FontLayoutTest(TempProfileTest):
    def setUp(self) -> None:
        super().setUp()
        self.machine = ct.load_machine(self.profile)
        self.font = FakeFont()
        self.opts = FAKE_OPTS

    def layout(self, **kw: object) -> ct.TextLayout:
        return ct.font_text(make_job(**kw), self.font, self.opts)

    def test_advance_and_center(self) -> None:
        layout = self.layout(lines=["AA"], char_height=20)
        firsts = [s[0] for s in layout.strokes if s[0][1] == layout.strokes[0][0][1]]
        self.assertAlmostEqual(firsts[1][0] - firsts[0][0], 0.7 * 20)
        xs = [p[0] for s in layout.strokes for p in s]
        ys = [p[1] for s in layout.strokes for p in s]
        self.assertAlmostEqual(min(xs) + max(xs), 0.0)
        self.assertAlmostEqual(min(ys) + max(ys), 0.0)
        self.assertEqual({c[1:] for c in self.font.calls}, {(20, 4, "fill", "auto")})

    def test_keeps_case_and_lines(self) -> None:
        layout = self.layout(lines=["e", "A"], char_height=20)
        self.assertEqual(layout.lines, ["e", "A"])
        tops = sorted({round(s[0][1], 6) for s in layout.strokes})
        self.assertAlmostEqual(tops[-1] - tops[0], ct.FONT_LINE_PITCH_EM * 20)

    def test_auto_size_fits(self) -> None:
        job = make_job(lines=["AAAAAAAAAA"])
        layout = ct.font_text(job, self.font, self.opts)
        heights = {c[1] for c in self.font.calls}
        self.assertLess(max(heights), ct.AUTO_CHAR_HEIGHT_CAP + 1e-9)
        self.assertLessEqual(ct.max_radius(layout.strokes), ct.text_limit_radius(job) - job.line_width / 2)
        ring = ct.font_text(make_job(lines=["A"]), FakeFont(), self.opts).strokes[0]
        self.assertAlmostEqual(ring[1][0] - ring[0][0], 0.5 * ct.AUTO_CHAR_HEIGHT_CAP)

    def test_outside_and_too_long(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "반경 밖"):
            self.layout(lines=["AAAA"], char_height=80)
        with self.assertRaisesRegex(ct.CreamTextError, "너무 김"):
            self.layout(lines=["A" * 60])
        with self.assertRaisesRegex(ct.CreamTextError, "최소"):
            self.layout(lines=["A"], char_height=1)

    def test_unsupported(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, r"지원하지 않는 문자: '가' \(폰트 fake에 없음\)"):
            self.layout(lines=["A가"])
        with self.assertRaisesRegex(ct.CreamTextError, "지원하지 않는 문자: '\\\\n'"):
            self.layout(lines=["A\nG28"])

    def test_gcode_closed_rings_and_e(self) -> None:
        job = make_job(lines=["A"], char_height=20)
        gcode, volumes = ct.build_gcode(self.machine, job, ct.font_text(job, self.font, self.opts))
        runs = extrude_runs(gcode)
        self.assertEqual(len(runs), 2)
        for run in runs:
            self.assertEqual(run[0], run[-1])
        length = sum(math.dist(a, b) for run in runs for a, b in zip(run, run[1:]))
        self.assertAlmostEqual(volumes[0], length * job.bead_area / 1000, places=3)
        lines = gcode.splitlines()
        self.assertEqual(lines[0], "; printc cream_text: A")
        down = [i for i, l in enumerate(lines) if l.startswith("G1 Z153.000")]
        for i in down:
            self.assertTrue(lines[i - 1].startswith("G1 X"), lines[i - 1])
            self.assertTrue(lines[i - 2].startswith("G1 Z158.000"), lines[i - 2])

    def test_fallback_marked(self) -> None:
        layout = self.layout(lines=["Ae."], char_height=20)
        self.assertEqual(layout.fallback_counts, {"embolden": 1, "center": 1})
        self.assertEqual(layout.fallback, {2, 3})
        self.assertEqual(ct.fallback_summary(layout.fallback_counts), "폴백: embolden 1자, center 1자")

    def test_outline_style_not_fallback(self) -> None:
        opts = ct.FontOptions(spec="fake", style="outline", fallback="none")
        layout = ct.font_text(make_job(lines=["A"], char_height=20), self.font, opts)
        self.assertEqual(layout.fallback_counts, {})
        self.assertEqual(self.font.calls[-1][3:], ("outline", "none"))

    def test_thin(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, r"'i' 40 mm 이상.*--char-height 40 제안"):
            self.layout(lines=["Ai"], char_height=20)
        with self.assertRaisesRegex(ct.CreamTextError, "'x' 해결 불가"):
            self.layout(lines=["Ax"], char_height=20)
        with self.assertRaisesRegex(ct.CreamTextError, "케이크 안 최대"):
            ct.font_text(make_job(lines=["AAAAAAAi"], char_height=20, cake_diameter=150), self.font, self.opts)
        opts = ct.FontOptions(spec="fake", allow_thin=True)
        layout = ct.font_text(make_job(lines=["Ai"], char_height=20), self.font, opts)
        self.assertEqual((len(layout.strokes), layout.skipped), (2, ["i"]))


class CliTest(TempProfileTest):
    def test_writes_out(self) -> None:
        code, _, err = self.run_cli("HAPPY", "--out", "out/a/job.gcode", *PROFILE, "--border")
        self.assertEqual(code, 0, err)
        gcode = (self.root / "out/a/job.gcode").read_text()
        self.assertIn("G1 Z153.000", gcode)  # height 기본값 + cfg gap

    def test_args_override_cfg(self) -> None:
        code, _, err = self.run_cli("HI", "--out", "out/b.gcode", *PROFILE, "--height", "100", "--gap", "5")
        self.assertEqual(code, 0, err)
        self.assertIn("G1 Z105.000", (self.root / "out/b.gcode").read_text())

    def test_rejects_path_escape(self) -> None:
        code, _, err = self.run_cli("HI", *PROFILE, "--out", "../evil.gcode")
        self.assertEqual(code, 1)
        self.assertIn("저장소 밖", err)
        code, _, err = self.run_cli("HI", *PROFILE, "--out", "out/job.txt")
        self.assertIn(".gcode", err)
        code, _, err = self.run_cli("HI", "--out", "out/j.gcode", "--profile", "/etc")
        self.assertIn("저장소 밖", err)

    def test_fallback_summary(self) -> None:
        code, out, err = self.run_cli("Ae.", "--out", "out/f.gcode", *PROFILE)
        self.assertEqual(code, 0, err)
        self.assertIn("폴백: embolden 1자, center 1자", out)

    def test_char_height_message(self) -> None:
        code, out, err = self.run_cli("HI", "--out", "out/f.gcode", *PROFILE, "--char-height", "20")
        self.assertEqual(code, 0, err)
        self.assertIn("글자 높이 20.0 mm(지정)", out)
        code, out, err = self.run_cli("HI", "--out", "out/f.gcode", *PROFILE)
        self.assertRegex(out, r"글자 높이 \d+\.\d mm\(자동\)")
        self.assertNotIn("읽기 어려울", err)

    def test_small_text_warning(self) -> None:
        small = {**FakeFont.SHAPES, "e": ("embolden", [_box(0.1, 0, 0.4, 0.5)], 0.5, False, 25.04)}
        with mock.patch.dict(FakeFont.SHAPES, small):
            code, out, err = self.run_cli("He", "--out", "out/f.gcode", *PROFILE, "--char-height", "20")
            self.assertEqual(code, 0, err)
            self.assertIn("경고: 글자가 작아 읽기 어려울 수 있음(글자 높이 20.0 mm < 권장 25.1 mm)", err)
            self.assertIn("--flower-inset", err)
            code, _, err = self.run_cli("He", "--out", "out/f.gcode", *PROFILE, "--char-height", "26")
            self.assertEqual(code, 0, err)
            self.assertNotIn("읽기 어려울", err)
            self.assertNotIn("계산 불가", err)

    def test_unrated_warning(self) -> None:
        code, _, err = self.run_cli("Hee.", "--out", "out/f.gcode", *PROFILE, "--char-height", "20")
        self.assertEqual(code, 0, err)
        self.assertIn("경고: 폴백으로 그린 글자의 권장 높이 계산 불가: 'e', '.'", err)
        self.assertNotIn("읽기 어려울", err)
        code, _, err = self.run_cli("HI", "--out", "out/f.gcode", *PROFILE, "--char-height", "20")
        self.assertEqual(code, 0, err)
        self.assertNotIn("계산 불가", err)

    def options_seen(self, *argv: str) -> ct.FontOptions:
        seen = []
        code, _, err = self.run_cli("A", "--out", "out/f.gcode", *PROFILE, *argv,
                                    factory=lambda opts: seen.append(opts) or FakeFont())
        self.assertEqual(code, 0, err)
        return seen[0]

    def test_default_font(self) -> None:
        self.assertEqual(self.options_seen(),
                         ct.FontOptions(ct.DEFAULT_FONT, "fill", ct.DEFAULT_WEIGHT, "auto", False))
        self.assertEqual(self.options_seen("--weight", "500", "--style", "outline", "--fallback", "center",
                                           "--allow-thin"),
                         ct.FontOptions(ct.DEFAULT_FONT, "outline", 500.0, "center", True))

    def test_other_font_weight(self) -> None:
        self.assertEqual(self.options_seen("--font", "Other").weight, None)
        self.assertEqual(self.options_seen("--font", "Other", "--weight", "300").weight, 300.0)

    def test_thin_cli(self) -> None:
        code, _, err = self.run_cli("Ai", "--out", "out/f.gcode", *PROFILE, "--char-height", "20")
        self.assertEqual(code, 1)
        self.assertIn("--char-height 40 제안", err)
        self.assertFalse((self.root / "out/f.gcode").exists())
        code, _, err = self.run_cli("Ai", "--out", "out/f.gcode", *PROFILE, "--char-height", "20", "--allow-thin")
        self.assertEqual(code, 0, err)
        self.assertIn("경고: 선 폭보다 가는 글자 건너뜀: 'i'", err)

    def test_preview(self) -> None:
        code, out, err = self.run_cli("Ae", "--out", "out/f.gcode", *PROFILE, "--border", "--flowers", "4",
                                      "--preview", "out/p/f.svg")
        self.assertEqual(code, 0, err)
        self.assertIn("미리 보기: out/p/f.svg", out)
        svg = (self.root / "out/p/f.svg").read_text()
        self.assertTrue(svg.startswith("<svg "))
        self.assertEqual(svg.count("<polyline"), 4)
        self.assertIn(ct.SVG_COLORS["fallback"], svg)
        self.assertIn("stroke-dasharray", svg)
        self.assertEqual(svg.count(f'fill="{ct.SVG_COLORS["flower"]}"'), 4)

    def test_preview_without_fallback(self) -> None:
        code, _, err = self.run_cli("HI", "--out", "out/f.gcode", *PROFILE, "--preview", "out/f.svg")
        self.assertEqual(code, 0, err)
        self.assertNotIn(ct.SVG_COLORS["fallback"], (self.root / "out/f.svg").read_text())

    def test_preview_path_rules(self) -> None:
        (self.root / "d.svg").mkdir()
        for path, msg in (("../p.svg", "저장소 밖"), ("out/p.png", "--preview 확장자는 .svg"), ("d.svg", "디렉터리")):
            code, _, err = self.run_cli("A", "--out", "out/f.gcode", *PROFILE, "--preview", path)
            self.assertEqual(code, 1)
            self.assertIn(msg, err)
        self.assertFalse((self.root / "out/f.gcode").exists())

    def test_missing_dependency(self) -> None:
        with mock.patch.dict(sys.modules, {"fontglyphs": None}):
            code, _, err = self.run_cli("A", "--out", "out/f.gcode", *PROFILE, factory=ct.open_font)
        self.assertEqual(code, 1)
        self.assertIn(ct.VENV_HINT, err)


HAS_FONT_DEPS = all(importlib.util.find_spec(m) for m in ("fontTools", "shapely"))
SYSTEM_FONT = Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf")
UPM = 1000


def build_box_font(path: Path, chars: str) -> None:
    """chars마다 굵은 사각형 글리프 하나인 정적 TTF."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    names = {ch: f"g{ord(ch)}" for ch in chars}
    order = [".notdef", *names.values()]
    glyphs = {}
    for name in order:
        pen = TTGlyphPen(None)
        if name != ".notdef":
            pen.moveTo((100, 0))
            for pt in ((100, 700), (600, 700), (600, 0)):
                pen.lineTo(pt)
            pen.closePath()
        glyphs[name] = pen.glyph()
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(ch): name for ch, name in names.items()})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (700, 0 if name == ".notdef" else 100) for name in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Box", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))


@unittest.skipUnless(HAS_FONT_DEPS, "폰트 의존 없음")
class OutlineFontCliTest(TempProfileTest):
    """실제 open_font 경로. 합성 폰트 파일을 경로로 넘겨 네트워크를 쓰지 않음."""

    def setUp(self) -> None:
        super().setUp()
        self.font = self.root / "box.ttf"
        build_box_font(self.font, "HI")

    def cli(self, *argv: str) -> tuple[int, str, str]:
        return self.run_cli(*argv, "--out", "out/r.gcode", *PROFILE, "--font", str(self.font),
                            factory=ct.open_font)

    def test_builds(self) -> None:
        code, _, err = self.cli("HI", "--border")
        self.assertEqual(code, 0, err)
        gcode = (self.root / "out/r.gcode").read_text()
        runs = extrude_runs(gcode)
        self.assertTrue(runs)
        for run in runs:
            self.assertLessEqual(math.hypot(run[0][0] - 160, run[0][1] - 117.5), 105)

    def test_constants_match_fontglyphs(self) -> None:
        import fontglyphs

        self.assertEqual(ct.FONT_STYLES, fontglyphs.STYLES)
        self.assertEqual(ct.FONT_FALLBACKS, tuple(fontglyphs.FALLBACKS))
        glyph = fontglyphs.OutlineFont(self.font).glyph_paths(" ", 20, 4)
        self.assertEqual(glyph.method, ct.NO_STROKE_METHOD)

    def test_font_errors(self) -> None:
        code, _, err = self.cli("HA")
        self.assertEqual(code, 1)
        self.assertIn("지원하지 않는 문자: 'A'", err)
        code, _, err = self.cli("HI", "--weight", "700")
        self.assertEqual(code, 1)
        self.assertIn("wght", err)

    def test_errors_converted(self) -> None:
        import fontglyphs

        font = ct.open_font(ct.FontOptions(spec=str(self.font)))
        bad = fontglyphs.FontGlyphError("bad glyph")
        with mock.patch.object(fontglyphs.OutlineFont, "glyph_paths", side_effect=bad), \
                self.assertRaisesRegex(ct.CreamTextError, "bad glyph"):
            font.glyph_paths("H", 20, 4)
        with self.assertRaisesRegex(ct.CreamTextError, "폰트 파일 없음"):
            ct.open_font(ct.FontOptions(spec=str(self.root / "none.ttf")))


@unittest.skipUnless(HAS_FONT_DEPS and SYSTEM_FONT.is_file(), "폰트 의존 또는 시스템 폰트 없음")
class SystemFontTest(TempProfileTest):
    def test_korean(self) -> None:
        code, _, err = self.run_cli("생일 축하", "--out", "out/k.gcode", *PROFILE, "--font", str(SYSTEM_FONT),
                                    "--allow-thin", "--preview", "out/k.svg", factory=ct.open_font)
        self.assertEqual(code, 0, err)
        gcode = (self.root / "out/k.gcode").read_text()
        self.assertTrue(any("E" in mv for mv in moves(gcode)))
        for run in extrude_runs(gcode):
            self.assertLessEqual(math.hypot(run[0][0] - 160, run[0][1] - 117.5), 105)


ACTIVE_PROFILE = ct.REPO_ROOT / ct.DEFAULT_PROFILE
CONTRACT_VARS = ("swap_x", "swap_y", "swap_z", "prime_x", "prime_y", "prime_z", "load_mm", "print_accel",
                 "travel_speed", "print_speed", "tool_count", "line_width", "line_height", "gap",
                 "piston_speed", "piston_max")


@unittest.skipUnless((ACTIVE_PROFILE / "printer.cfg").is_file(), "hardware/active 없음")
class ActiveProfileTest(unittest.TestCase):
    def test_contract_and_build(self) -> None:
        cfg = ct.load_config(ACTIVE_PROFILE)
        missing = [v for v in CONTRACT_VARS if not cfg.has_option(ct.CFG_MACRO, f"variable_{v}")]
        self.assertEqual(missing, [])
        machine = ct.load_machine(ACTIVE_PROFILE)
        build(machine, make_job(line_width=machine.line_width, line_height=machine.line_height,
                                gap=machine.gap, border=True, flowers=8))


if __name__ == "__main__":
    unittest.main()
