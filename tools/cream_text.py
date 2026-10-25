#!/usr/bin/env python3
"""문구를 생크림 레터링 gcode로 변환함.

기계 값은 프로필 cfg에서 읽음. 작업 값(케이크 치수, 글씨 크기 등)은 인자로 받음.
선 폭, 선 높이, 깍지 간격은 cfg 값을 쓰고 인자로 덮어쓸 수 있음.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = Path("hardware/active")
CFG_MACRO = "gcode_macro _CREAM_CFG"
SAVE_CONFIG_PREFIX = "#*# "
SAVE_CONFIG_LINE = re.compile(r"^(\[|\w+\s*[:=]|\s)")
AXES = ("x", "y", "z")
TEXT_TOOL, BORDER_TOOL, FLOWER_TOOL = 0, 1, 2
MAX_TEXT_CHARS = 64
AUTO_CHAR_HEIGHT_CAP = 30.0
MIN_CHAR_HEIGHT_PER_LINE_WIDTH = 2.5
BORDER_SEGMENT_MM = 2.0
# 꽃은 점으로 짜 선보다 넓게 퍼지므로 글씨와 선 폭 2배 거리를 둠.
FLOWER_CLEARANCE_LINE_WIDTHS = 2.0
# 3호 케이크 기본 작업 값.
DEFAULT_CAKE_DIAMETER = 210.0
DEFAULT_CAKE_HEIGHT = 150.0

Point = tuple[float, float]
Stroke = list[Point]

# 글꼴 격자: 글자 높이 6, 기본 폭 4. 획은 꺾은선.
GLYPH_HEIGHT = 6.0
GLYPH_WIDTH = 4.0
CHAR_GAP = 1.5
LINE_GAP = 3.0
_O = [(1, 0), (0, 1), (0, 5), (1, 6), (3, 6), (4, 5), (4, 1), (3, 0), (1, 0)]
_P = [(0, 0), (0, 6), (3, 6), (4, 5), (4, 4), (3, 3), (0, 3)]
_DOT = [(2, 0), (2, 0.4)]
GLYPHS: dict[str, list[Stroke]] = {
    " ": [],
    "A": [[(0, 0), (2, 6), (4, 0)], [(0.67, 2), (3.33, 2)]],
    "B": [_P, [(3, 3), (4, 2), (4, 1), (3, 0), (0, 0)]],
    "C": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1)]],
    "D": [[(0, 0), (0, 6), (2.5, 6), (4, 4.5), (4, 1.5), (2.5, 0), (0, 0)]],
    "E": [[(4, 6), (0, 6), (0, 0), (4, 0)], [(0, 3), (3, 3)]],
    "F": [[(4, 6), (0, 6), (0, 0)], [(0, 3), (3, 3)]],
    "G": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 3), (2, 3)]],
    "H": [[(0, 0), (0, 6)], [(4, 0), (4, 6)], [(0, 3), (4, 3)]],
    "I": [[(1, 6), (3, 6)], [(2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "J": [[(4, 6), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "K": [[(0, 0), (0, 6)], [(4, 6), (0, 2)], [(1.33, 3.33), (4, 0)]],
    "L": [[(0, 6), (0, 0), (4, 0)]],
    "M": [[(0, 0), (0, 6), (2, 3), (4, 6), (4, 0)]],
    "N": [[(0, 0), (0, 6), (4, 0), (4, 6)]],
    "O": [_O],
    "P": [_P],
    "Q": [_O, [(2.5, 1.5), (4, 0)]],
    "R": [_P, [(2, 3), (4, 0)]],
    "S": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 4), (1, 3), (3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "T": [[(0, 6), (4, 6)], [(2, 6), (2, 0)]],
    "U": [[(0, 6), (0, 1), (1, 0), (3, 0), (4, 1), (4, 6)]],
    "V": [[(0, 6), (2, 0), (4, 6)]],
    "W": [[(0, 6), (1, 0), (2, 4), (3, 0), (4, 6)]],
    "X": [[(0, 6), (4, 0)], [(4, 6), (0, 0)]],
    "Y": [[(0, 6), (2, 3), (4, 6)], [(2, 3), (2, 0)]],
    "Z": [[(0, 6), (4, 6), (0, 0), (4, 0)]],
    "0": [[(1.25, 0), (0.5, 1), (0.5, 5), (1.25, 6), (2.75, 6), (3.5, 5), (3.5, 1), (2.75, 0), (1.25, 0)]],
    "1": [[(1, 5), (2, 6), (2, 0)], [(1, 0), (3, 0)]],
    "2": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (0, 0), (4, 0)]],
    "3": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1.5, 3)],
          [(3, 3), (4, 2), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "4": [[(3, 0), (3, 6), (0, 2), (4, 2)]],
    "5": [[(4, 6), (0, 6), (0, 3.5), (3, 3.5), (4, 2.5), (4, 1), (3, 0), (1, 0), (0, 1)]],
    "6": [[(4, 5), (3, 6), (1, 6), (0, 5), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2.5), (3, 3.5), (0, 3.5)]],
    "7": [[(0, 6), (4, 6), (1.5, 0)]],
    "8": [[(1, 3), (0, 4), (0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (3, 3), (1, 3),
           (0, 2), (0, 1), (1, 0), (3, 0), (4, 1), (4, 2), (3, 3)]],
    "9": [[(0, 1), (1, 0), (3, 0), (4, 1), (4, 5), (3, 6), (1, 6), (0, 5), (0, 3.5), (1, 2.5), (4, 2.5)]],
    ".": [_DOT],
    ",": [[(2, 0.5), (1.5, -1)]],
    "!": [[(2, 6), (2, 2)], _DOT],
    "?": [[(0, 5), (1, 6), (3, 6), (4, 5), (4, 4), (2, 2.5), (2, 1.5)], _DOT],
    "-": [[(1, 3), (3, 3)]],
    "+": [[(0.5, 3), (3.5, 3)], [(2, 1.5), (2, 4.5)]],
    "'": [[(2, 6), (2, 4.5)]],
    ":": [[(2, 1), (2, 1.4)], [(2, 4), (2, 4.4)]],
    "/": [[(0, 0), (4, 6)]],
    "♥": [[(2, 0), (0, 3), (0, 4.5), (1, 5.5), (2, 4.5), (3, 5.5), (4, 4.5), (4, 3), (2, 0)]],
}


class CreamTextError(Exception):
    """사용자에게 보일 생성 오류."""


@dataclass(frozen=True)
class Machine:
    limits: dict[str, tuple[float, float]]
    center: Point
    travel_speed: float
    print_speed: float
    print_accel: float
    max_accel: float
    tool_count: int
    filament_area: float
    max_cross_section: float
    line_width: float
    line_height: float
    gap: float


@dataclass(frozen=True)
class Job:
    lines: list[str]
    cake_diameter: float
    height: float
    gap: float
    lift: float
    char_height: float | None
    line_width: float
    line_height: float
    margin: float
    border: bool
    border_inset: float
    flowers: int
    flower_inset: float
    flower_ml: float

    @property
    def bead_area(self) -> float:
        return math.pi / 4 * self.line_width * self.line_height

    @property
    def cake_radius(self) -> float:
        return self.cake_diameter / 2


# ---------- cfg 읽기 ----------

def _cfg_text(path: Path) -> str:
    """SAVE_CONFIG 블록(#*#)의 값도 살려 읽음. Klipper가 그 값을 우선 적용하기 때문."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(SAVE_CONFIG_PREFIX):
            rest = line[len(SAVE_CONFIG_PREFIX):]
            if SAVE_CONFIG_LINE.match(rest):
                line = rest
        lines.append(line)
    return "\n".join(lines) + "\n"


def _include_paths(cfg_path: Path, pattern: str, profile_dir: Path) -> list[Path]:
    """기계 값은 프로필 안에만 있으므로 프로필 밖 include는 건너뜀."""
    target = (cfg_path.parent / pattern).resolve()
    if not target.is_relative_to(profile_dir):
        return []
    rel = target.relative_to(profile_dir).as_posix()
    return sorted(p for p in profile_dir.glob(rel) if p.is_file())


def _new_parser() -> configparser.ConfigParser:
    return configparser.ConfigParser(
        strict=False,
        interpolation=None,
        inline_comment_prefixes=("#", ";"),
        comment_prefixes=("#", ";"),
        delimiters=(":", "="),
    )


def _read_cfg(path: Path, profile_dir: Path, parser: configparser.ConfigParser, seen: set[Path]) -> None:
    """include를 만난 자리에서 따라 읽고 뒤에 읽은 값이 앞 값을 덮음.

    한계: 한 파일 안 같은 이름 섹션은 처음 나온 자리에서 합쳐 읽음. 그래서 include 앞뒤에
    같은 섹션이 있으면 include 뒤 값도 include 값에 덮임(Klipper는 뒤 값이 이김).
    """
    path = path.resolve()
    if path in seen:
        return
    seen.add(path)
    single = _new_parser()
    try:
        single.read_string(_cfg_text(path), source=str(path))
    except (OSError, configparser.Error) as exc:
        raise CreamTextError(f"cfg 읽기 실패: {path}: {exc}") from exc
    for section in single.sections():
        if section.startswith("include "):
            for inc in _include_paths(path, section[len("include "):].strip(), profile_dir):
                _read_cfg(inc, profile_dir, parser, seen)
            continue
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in single.items(section):
            parser.set(section, key, value)


def load_config(profile_dir: Path) -> configparser.ConfigParser:
    printer_cfg = profile_dir / "printer.cfg"
    if not printer_cfg.is_file():
        raise CreamTextError(f"printer.cfg 없음: {printer_cfg}")
    parser = _new_parser()
    _read_cfg(printer_cfg, profile_dir.resolve(), parser, set())
    return parser


def _float(cfg: configparser.ConfigParser, section: str, key: str, default: float | None = None) -> float:
    if not cfg.has_option(section, key):
        if default is None:
            raise CreamTextError(f"cfg 값 없음: [{section}] {key}")
        return default
    raw = cfg.get(section, key)
    try:
        # variable_ 값은 Klipper가 파이썬 리터럴로 해석함.
        value = ast.literal_eval(raw) if key.startswith("variable_") else float(raw)
        return float(value)
    except (ValueError, SyntaxError, TypeError) as exc:
        raise CreamTextError(f"cfg 값이 숫자가 아님: [{section}] {key} = {raw}") from exc


def _var(cfg: configparser.ConfigParser, name: str) -> float:
    return _float(cfg, CFG_MACRO, f"variable_{name}")


def load_machine(profile_dir: Path, center: Point | None = None) -> Machine:
    cfg = load_config(profile_dir)
    if not cfg.has_section(CFG_MACRO):
        raise CreamTextError(f"[{CFG_MACRO}] 없음: {profile_dir}")
    limits = {
        axis: (_float(cfg, f"stepper_{axis}", "position_min", 0.0),
               _float(cfg, f"stepper_{axis}", "position_max"))
        for axis in AXES
    }
    if center is None:
        try:
            center = (_var(cfg, "center_x"), _var(cfg, "center_y"))
        except CreamTextError as exc:
            raise CreamTextError(f"{exc}. cream_vars.cfg에 추가하거나 --center 지정") from exc
    tool_count = _var(cfg, "tool_count")
    if not tool_count.is_integer() or tool_count < 1:
        raise CreamTextError(f"variable_tool_count는 1 이상 정수: {tool_count:g}")
    nozzle = _float(cfg, "extruder", "nozzle_diameter")
    filament = _float(cfg, "extruder", "filament_diameter")
    return Machine(
        limits=limits,
        center=center,
        travel_speed=_var(cfg, "travel_speed"),
        print_speed=_var(cfg, "print_speed"),
        print_accel=_var(cfg, "print_accel"),
        max_accel=_float(cfg, "printer", "max_accel"),
        tool_count=int(tool_count),
        filament_area=math.pi / 4 * filament ** 2,
        # Klipper 기본값과 같은 식.
        max_cross_section=_float(cfg, "extruder", "max_extrude_cross_section", 4.0 * nozzle ** 2),
        line_width=_var(cfg, "line_width"),
        line_height=_var(cfg, "line_height"),
        gap=_var(cfg, "gap"),
    )


# ---------- 모양 ----------

def validate_lines(lines: list[str]) -> list[str]:
    result = [line.strip().upper() for line in lines]
    if not any(result):
        raise CreamTextError("문구가 비어 있음")
    if sum(len(line) for line in result) > MAX_TEXT_CHARS:
        raise CreamTextError(f"문구가 {MAX_TEXT_CHARS}자 초과")
    unsupported = sorted({ch for line in result for ch in line if ch not in GLYPHS})
    if unsupported:
        shown = ", ".join(repr(ch) for ch in unsupported)
        raise CreamTextError(f"지원하지 않는 문자: {shown} (영문, 숫자, 기호 {''.join(sorted(GLYPHS)).strip()})")
    return [line for line in result if line]


def _shift(strokes: list[Stroke], dx: float, dy: float) -> list[Stroke]:
    return [[(x + dx, y + dy) for x, y in stroke] for stroke in strokes]


def _ink_mid(strokes: list[Stroke], index: int) -> float:
    values = [p[index] for stroke in strokes for p in stroke]
    return (min(values) + max(values)) / 2 if values else 0.0


def text_strokes_unit(lines: list[str]) -> list[Stroke]:
    """글자 높이 1로 배치함. 줄마다 가로, 전체를 세로로 획 기준 중심 정렬."""
    strokes: list[Stroke] = []
    for row, line in enumerate(lines):
        y0 = -row * (GLYPH_HEIGHT + LINE_GAP)
        row_strokes = [
            [((col * (GLYPH_WIDTH + CHAR_GAP) + x) / GLYPH_HEIGHT, (y0 + y) / GLYPH_HEIGHT) for x, y in glyph]
            for col, ch in enumerate(line)
            for glyph in GLYPHS[ch]
        ]
        strokes += _shift(row_strokes, -_ink_mid(row_strokes, 0), 0.0)
    return _shift(strokes, 0.0, -_ink_mid(strokes, 1))


def max_radius(strokes: list[Stroke]) -> float:
    return max((math.hypot(x, y) for stroke in strokes for x, y in stroke), default=0.0)


def text_limit_radius(job: Job) -> float:
    """글씨가 들어갈 수 있는 반경. 테두리, 꽃 안쪽으로 제한함."""
    limit = job.cake_radius - job.margin
    if job.border:
        limit = min(limit, job.cake_radius - job.border_inset - job.line_width)
    if job.flowers:
        limit = min(limit, job.cake_radius - job.flower_inset - FLOWER_CLEARANCE_LINE_WIDTHS * job.line_width)
    return limit


def scale_text(job: Job, unit: list[Stroke]) -> list[Stroke]:
    reach = max_radius(unit)
    room = text_limit_radius(job) - job.line_width / 2
    fit = room / reach if reach > 0 else 0.0
    min_height = MIN_CHAR_HEIGHT_PER_LINE_WIDTH * job.line_width
    if job.char_height is None:
        height = min(fit, AUTO_CHAR_HEIGHT_CAP)
        if height < min_height:
            raise CreamTextError(f"문구가 너무 김: 가능한 글자 높이 {fit:.1f} mm < 최소 {min_height:.1f} mm")
    else:
        height = job.char_height
        if height < min_height:
            raise CreamTextError(f"글자 높이 {height:g} mm < 최소 {min_height:.1f} mm(선 폭 기준)")
        if height > fit:
            raise CreamTextError(f"글씨가 케이크 반경 밖: 글자 높이 최대 {max(fit, 0):.1f} mm")
    return [[(x * height, y * height) for x, y in stroke] for stroke in unit]


def border_stroke(job: Job) -> Stroke:
    radius = job.cake_radius - job.border_inset
    if radius <= job.line_width:
        raise CreamTextError("테두리 반경이 너무 작음")
    count = max(12, math.ceil(2 * math.pi * radius / BORDER_SEGMENT_MM))
    return [(radius * math.cos(2 * math.pi * i / count), radius * math.sin(2 * math.pi * i / count))
            for i in range(count + 1)]


def flower_points(job: Job) -> list[Point]:
    radius = job.cake_radius - job.flower_inset
    if radius <= 0:
        raise CreamTextError("꽃 반경이 0 이하")
    return [(radius * math.cos(2 * math.pi * i / job.flowers), radius * math.sin(2 * math.pi * i / job.flowers))
            for i in range(job.flowers)]


# ---------- gcode ----------

class GcodeWriter:
    """이동마다 범위를 확인하며 gcode를 만듦."""

    def __init__(self, machine: Machine) -> None:
        self.machine = machine
        self.lines: list[str] = []
        self.extruded: dict[int, float] = {}
        self.tool = -1
        self.position: dict[str, float] = {}

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def _check(self, axis: str, value: float) -> None:
        low, high = self.machine.limits[axis]
        if not low <= value <= high:
            raise CreamTextError(f"범위 초과: {axis.upper()}{value:.3f} (허용 {low}–{high})")

    def move(self, *, x: float | None = None, y: float | None = None, z: float | None = None,
             e: float = 0.0, speed: float) -> None:
        words = []
        for axis, value in (("x", x), ("y", y), ("z", z)):
            if value is not None and value != self.position.get(axis):
                self._check(axis, value)
                words.append(f"{axis.upper()}{value:.3f}")
                self.position[axis] = value
        if not words and not e:
            return
        if e:
            words.append(f"E{e:.5f}")
            self.extruded[self.tool] = self.extruded.get(self.tool, 0.0) + e
        self.emit(f"G1 {' '.join(words)} F{speed * 60:.0f}")

    def set_tool(self, tool: int) -> None:
        if tool >= self.machine.tool_count:
            raise CreamTextError(f"카트리지 T{tool} 없음: tool_count {self.machine.tool_count}")
        self.tool = tool
        # 매크로가 헤드를 옮기므로 위치를 모름으로 둠.
        self.position.clear()
        # 장전(CREAM_LOAD)은 교체 후 RESUME이 수행함.
        self.emit(f"T{tool}")
        self.emit("PRIME")
        self.emit(f"SET_VELOCITY_LIMIT ACCEL={self.machine.print_accel:g}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _to_machine(machine: Machine, point: Point) -> Point:
    return machine.center[0] + point[0], machine.center[1] + point[1]


def _approach(w: GcodeWriter, point: Point, write_z: float, travel_z: float) -> None:
    """들어 올린 채 point 위로 가서 짜는 높이로 내림."""
    speed = w.machine.travel_speed
    w.move(z=travel_z, speed=speed)
    w.move(x=point[0], y=point[1], speed=speed)
    w.move(z=write_z, speed=speed)


def write_stroke(w: GcodeWriter, job: Job, stroke: Stroke, write_z: float, travel_z: float) -> None:
    m = w.machine
    e_per_mm = job.bead_area / m.filament_area
    start = _to_machine(m, stroke[0])
    _approach(w, start, write_z, travel_z)
    prev = start
    for point in stroke[1:]:
        cur = _to_machine(m, point)
        length = math.dist(prev, cur)
        if length > 0:
            w.move(x=cur[0], y=cur[1], e=length * e_per_mm, speed=m.print_speed)
        prev = cur
    w.move(z=travel_z, speed=m.travel_speed)


def write_dab(w: GcodeWriter, job: Job, point: Point, write_z: float, travel_z: float) -> None:
    m = w.machine
    piston_mm = job.flower_ml * 1000 / m.filament_area
    # 글씨와 같은 부피 유량으로 짬.
    piston_speed = m.print_speed * job.bead_area / m.filament_area
    _approach(w, _to_machine(m, point), write_z, travel_z)
    w.move(e=piston_mm, speed=piston_speed)
    w.move(z=travel_z, speed=m.travel_speed)


def check_job(machine: Machine, job: Job) -> None:
    if job.bead_area > machine.max_cross_section:
        raise CreamTextError(
            f"선 단면 {job.bead_area:.2f} mm² > max_extrude_cross_section {machine.max_cross_section:.2f} mm²")
    for name in ("cake_diameter", "height", "line_width", "line_height"):
        if getattr(job, name) <= 0:
            raise CreamTextError(f"{name}는 0보다 커야 함")
    if job.char_height is not None and job.char_height <= 0:
        raise CreamTextError("char_height는 0보다 커야 함")
    for name in ("gap", "lift", "margin", "border_inset", "flower_inset"):
        if getattr(job, name) < 0:
            raise CreamTextError(f"{name}는 0 이상이어야 함")
    for name, used in (("margin", True), ("border_inset", job.border), ("flower_inset", bool(job.flowers))):
        if used and getattr(job, name) >= job.cake_radius:
            raise CreamTextError(f"{name} {getattr(job, name):g} mm >= 케이크 반경 {job.cake_radius:g} mm")
    if job.flowers < 0 or (job.flowers and job.flower_ml <= 0):
        raise CreamTextError("flowers, flower_ml 값 오류")


def build_gcode(machine: Machine, job: Job) -> tuple[str, dict[int, float]]:
    """gcode와 카트리지별 짜는 크림 부피(mL, PRIME 제외)를 반환함."""
    check_job(machine, job)
    lines = validate_lines(job.lines)
    text = scale_text(job, text_strokes_unit(lines))
    write_z = job.height + job.gap
    travel_z = write_z + job.lift

    w = GcodeWriter(machine)
    w.emit(f"; printc cream_text: {' / '.join(lines)}")
    w.emit("G28")
    w.emit("G90")
    w.emit("M83")
    w.set_tool(TEXT_TOOL)
    for stroke in text:
        write_stroke(w, job, stroke, write_z, travel_z)
    if job.border:
        w.set_tool(BORDER_TOOL)
        write_stroke(w, job, border_stroke(job), write_z, travel_z)
    if job.flowers:
        w.set_tool(FLOWER_TOOL)
        for point in flower_points(job):
            write_dab(w, job, point, write_z, travel_z)
    w.emit(f"SET_VELOCITY_LIMIT ACCEL={machine.max_accel:g}")
    w.move(z=travel_z, speed=machine.travel_speed)
    # 판을 앞으로 내어 케이크를 꺼냄. Y 최대가 판 앞쪽이라고 가정함(PRIME의 prime_y와 같은 쪽).
    w.move(y=machine.limits["y"][1], speed=machine.travel_speed)
    volumes = {tool: mm * machine.filament_area / 1000 for tool, mm in w.extruded.items()}
    return w.text(), volumes


# ---------- CLI ----------

def resolve_inside(path: Path, root: Path, what: str) -> Path:
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root):
        raise CreamTextError(f"{what} 경로가 저장소 밖: {path}")
    return resolved


def resolve_out(path: Path, root: Path) -> Path:
    out = resolve_inside(path, root, "--out")
    if out.suffix != ".gcode":
        raise CreamTextError("--out 확장자는 .gcode")
    if out.is_dir():
        raise CreamTextError(f"--out이 디렉터리: {out}")
    return out


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="문구를 생크림 레터링 gcode로 변환함. 인자 하나가 한 줄.")
    p.add_argument("text", nargs="+", help="문구 줄(영문, 숫자, 기호)")
    p.add_argument("--out", type=Path, required=True, help="출력 .gcode (저장소 안)")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE, help="프로필 디렉터리 (저장소 안)")
    p.add_argument("--cake-diameter", type=float, default=DEFAULT_CAKE_DIAMETER,
                   help="케이크 지름 mm. 기본 3호")
    p.add_argument("--height", type=float, default=DEFAULT_CAKE_HEIGHT,
                   help="케이크 높이 mm (판 윗면 기준). 기본 3호")
    p.add_argument("--gap", type=float, help="케이크 윗면에서 깍지 끝까지 mm. 생략 시 _CREAM_CFG gap")
    p.add_argument("--lift", type=float, default=5.0, help="획 사이 이동 시 추가 높이 mm")
    p.add_argument("--char-height", type=float, help="글자 높이 mm. 생략 시 맞춤")
    p.add_argument("--line-width", type=float, help="크림 선 폭 mm. 생략 시 _CREAM_CFG line_width")
    p.add_argument("--line-height", type=float, help="크림 선 높이 mm. 생략 시 _CREAM_CFG line_height")
    p.add_argument("--margin", type=float, default=10.0, help="케이크 가장자리에서 글씨까지 최소 거리 mm")
    p.add_argument("--border", action="store_true", help=f"T{BORDER_TOOL} 테두리 추가")
    p.add_argument("--border-inset", type=float, default=8.0, help="가장자리에서 테두리까지 mm")
    p.add_argument("--flowers", type=int, default=0, help=f"T{FLOWER_TOOL} 꽃 개수")
    p.add_argument("--flower-inset", type=float, default=20.0, help="가장자리에서 꽃 중심까지 mm")
    p.add_argument("--flower-ml", type=float, default=0.5, help="꽃 하나 크림 mL")
    p.add_argument("--center", type=float, nargs=2, metavar=("X", "Y"),
                   help="케이크 중심. 생략 시 _CREAM_CFG center_x, center_y")
    return p.parse_args(argv)


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


def main(argv: list[str] | None = None, root: Path = REPO_ROOT) -> int:
    args = parse_args(argv)
    root = root.resolve()
    try:
        profile = resolve_inside(args.profile, root, "--profile")
        out = resolve_out(args.out, root)
        machine = load_machine(profile, tuple(args.center) if args.center else None)
        job = Job(
            lines=args.text, cake_diameter=args.cake_diameter, height=args.height,
            gap=_or(args.gap, machine.gap), lift=args.lift, char_height=args.char_height,
            line_width=_or(args.line_width, machine.line_width),
            line_height=_or(args.line_height, machine.line_height), margin=args.margin, border=args.border,
            border_inset=args.border_inset, flowers=args.flowers, flower_inset=args.flower_inset,
            flower_ml=args.flower_ml,
        )
        gcode, volumes = build_gcode(machine, job)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(gcode, encoding="utf-8")
    except (CreamTextError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    summary = ", ".join(f"T{t} {v:.1f} mL" for t, v in sorted(volumes.items()))
    print(f"생성: {out.relative_to(root)} (짜는 크림 {summary}, PRIME 제외)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
