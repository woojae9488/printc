"""cream_text 단위 테스트. 계약 형식의 임시 프로필로 검증함."""

from __future__ import annotations

import io
import math
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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


class TempProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.profile = make_profile(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()


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
        gcode, volumes = ct.build_gcode(self.machine, make_job(border=True, flowers=8))
        lines = gcode.splitlines()
        self.assertEqual(lines[1], "G28")
        for tool in ("T0", "T1", "T2"):
            i = lines.index(tool)
            self.assertEqual(lines[i + 1], "PRIME")
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
        gcode, _ = ct.build_gcode(self.machine, make_job())
        lines = gcode.splitlines()
        after = lines[lines.index("PRIME") + 1:]
        first = next(l for l in after if l.startswith("G1"))
        self.assertTrue(first.startswith("G1 Z158.000"), first)

    def test_text_centered(self) -> None:
        gcode, _ = ct.build_gcode(self.machine, make_job(lines=["HI"], char_height=20))
        pts = [(m["X"], m["Y"]) for m in moves(gcode) if "E" in m]
        xs = [p[0] for p in pts]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 160.0, places=1)

    def test_text_outside_cake(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "반경 밖"):
            ct.build_gcode(self.machine, make_job(lines=["HELLO"], char_height=60))

    def test_char_height_invalid(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "char_height"):
            ct.build_gcode(self.machine, make_job(char_height=0))
        with self.assertRaisesRegex(ct.CreamTextError, "최소"):
            ct.build_gcode(self.machine, make_job(char_height=1))

    def test_inset_beyond_radius(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "flower_inset .* 반지름"):
            ct.build_gcode(self.machine, make_job(flowers=4, flower_inset=120))
        with self.assertRaisesRegex(ct.CreamTextError, "border_inset .* 반지름"):
            ct.build_gcode(self.machine, make_job(border=True, border_inset=105))

    def test_too_long(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "너무 김"):
            ct.build_gcode(self.machine, make_job(lines=["ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"], cake_diameter=150))

    def test_unsupported_char(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "지원하지 않는 문자: '생'"):
            ct.build_gcode(self.machine, make_job(lines=["생일 축하"]))
        with self.assertRaisesRegex(ct.CreamTextError, "지원하지 않는 문자"):
            ct.build_gcode(self.machine, make_job(lines=["A\nG28"]))

    def test_height_out_of_range(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "범위 초과: Z"):
            ct.build_gcode(self.machine, make_job(height=255))

    def test_cake_outside_axis(self) -> None:
        machine = ct.Machine(**{**self.machine.__dict__, "center": (10.0, 117.5)})
        with self.assertRaisesRegex(ct.CreamTextError, "범위 초과: X"):
            ct.build_gcode(machine, make_job(lines=["HI"]))

    def test_tool_count(self) -> None:
        machine = ct.Machine(**{**self.machine.__dict__, "tool_count": 2})
        with self.assertRaisesRegex(ct.CreamTextError, "T2 없음"):
            ct.build_gcode(machine, make_job(flowers=8))

    def test_cross_section(self) -> None:
        with self.assertRaisesRegex(ct.CreamTextError, "max_extrude_cross_section"):
            ct.build_gcode(self.machine, make_job(line_width=20, line_height=20))


class CliTest(TempProfileTest):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = ct.main(list(argv), root=self.root)
        return code, err.getvalue()

    def test_writes_out(self) -> None:
        code, err = self.run_cli("HAPPY", "--out", "out/a/job.gcode", "--profile", "hardware/test", "--border")
        self.assertEqual(code, 0, err)
        gcode = (self.root / "out/a/job.gcode").read_text()
        self.assertIn("G1 Z153.000", gcode)  # height 기본값 + cfg gap

    def test_args_override_cfg(self) -> None:
        code, err = self.run_cli("HI", "--out", "out/b.gcode", "--profile", "hardware/test",
                                 "--height", "100", "--gap", "5")
        self.assertEqual(code, 0, err)
        self.assertIn("G1 Z105.000", (self.root / "out/b.gcode").read_text())

    def test_rejects_path_escape(self) -> None:
        base = ["HI", "--profile", "hardware/test"]
        code, err = self.run_cli(*base, "--out", "../evil.gcode")
        self.assertEqual(code, 1)
        self.assertIn("저장소 밖", err)
        code, err = self.run_cli(*base, "--out", "out/job.txt")
        self.assertIn(".gcode", err)
        code, err = self.run_cli("HI", "--out", "out/j.gcode", "--profile", "/etc")
        self.assertIn("저장소 밖", err)


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
        ct.build_gcode(machine, make_job(line_width=machine.line_width, line_height=machine.line_height,
                                         gap=machine.gap, border=True, flowers=8))


if __name__ == "__main__":
    unittest.main()
