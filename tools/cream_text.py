#!/usr/bin/env python3
"""문구를 생크림 레터링 gcode로 변환함.

기계 값은 프로필 cfg에서 읽음. 작업 값(케이크 치수, 글씨 크기 등)은 인자로 받음.
선 폭, 선 높이, 깍지 간격은 cfg 값을 쓰고 인자로 덮어쓸 수 있음.
글씨는 폰트 윤곽선으로 그림. 사용법은 docs/lettering.md.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import math
import os
import re
import sys
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Protocol

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
# --font 생략 시 폰트. DEFAULT_WEIGHT는 가변 폰트 wght 값, 가변이 아니면 None.
DEFAULT_FONT = "Black Han Sans"
DEFAULT_WEIGHT: float | None = None
# 글자 높이는 em 높이. 줄 간격도 em 비율.
FONT_LINE_PITCH_EM = 1.25
FONT_STYLES = ("fill", "outline")
FONT_FALLBACKS = ("auto", "none", "embolden", "center")
# 요약에 보이는 순서. 폰트가 쓴 방법이 스타일과 다르면 폴백임.
FALLBACK_ORDER = ("embolden", "center", "line")
NO_STROKE_METHOD = "none"
# 폰트 윤곽 크기는 글자 높이에 비례하지 않으므로(선 폭만큼 안쪽) 줄여 가며 다시 맞춤.
FIT_TRIES = 6
FIT_SHRINK = 0.98
VENV_HINT = ("설치: python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt, "
             "실행: .venv/bin/python tools/cream_text.py ...")
SVG_COLORS = {"text": "#8d6e63", "fallback": "#e65100", "border": "#1e88e5", "flower": "#d81b60",
              "travel": "#9e9e9e", "cake": "#bdbdbd", "paper": "#f6efe2", "outline": "#000000"}

Point = tuple[float, float]
Stroke = list[Point]


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


@dataclass(frozen=True)
class FontOptions:
    spec: str
    style: str = "fill"
    weight: float | None = None
    fallback: str = "auto"
    allow_thin: bool = False


class GlyphLike(Protocol):
    strokes: list[Stroke]
    advance: float
    too_thin: bool
    method: str
    min_char_height: float | None


class FontLike(Protocol):
    """fontglyphs.OutlineFont 계약. 시험에서 가짜 폰트로 바꿔 끼움."""

    def has_glyph(self, ch: str) -> bool: ...

    def glyph_paths(self, ch: str, char_height: float, line_width: float, style: str = "fill",
                    fallback: str = "auto") -> GlyphLike: ...


FontFactory = Callable[[FontOptions], FontLike]


@dataclass
class TextLayout:
    """확인한 문구 줄, 짤 글씨 획, 폰트 처리 결과. fallback은 폴백으로 그린 획 번호."""

    lines: list[str]
    strokes: list[Stroke]
    fallback: set[int] = field(default_factory=set)
    fallback_counts: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    char_height: float = 0.0
    auto_height: bool = False
    # 쓴 글자가 폴백 없이 채워지는 em 높이 중 최댓값. 모르면 None.
    readable_height: float | None = None
    # 폴백으로 그렸으나 그 높이를 모르는 글자.
    unrated: list[str] = field(default_factory=list)
    # 획마다 카트리지. 비면 모두 TEXT_TOOL. 이어진 같은 카트리지 획은 전환 없이 짬.
    tools: list[int] = field(default_factory=list)
    # 획마다 미리 보기 색(#rrggbb). 비면 기본 색.
    colors: list[str] = field(default_factory=list)

    def check(self) -> None:
        """획마다 값이 있는 목록은 획 수와 길이가 같아야 함."""
        for name in ("tools", "colors"):
            values = getattr(self, name)
            if values and len(values) != len(self.strokes):
                raise CreamTextError(f"레이아웃 {name} 길이 {len(values)} != 획 수 {len(self.strokes)}")

    def tool_for(self, index: int) -> int:
        return self.tools[index] if self.tools else TEXT_TOOL

    @property
    def too_small(self) -> bool:
        return self.readable_height is not None and self.char_height < self.readable_height


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

def validate_lines(lines: list[str], font: FontLike, font_name: str) -> list[str]:
    result = [line.strip() for line in lines]
    if not any(result):
        raise CreamTextError("문구가 비어 있음")
    if sum(len(line) for line in result) > MAX_TEXT_CHARS:
        raise CreamTextError(f"문구가 {MAX_TEXT_CHARS}자 초과")
    # 공백 외 제어, 공백 문자는 막음. 줄바꿈이 gcode 주석 밖으로 새지 않게 함.
    unsupported = sorted({ch for line in result for ch in line
                          if ch != " " and not (ch.isprintable() and font.has_glyph(ch))})
    if unsupported:
        shown = ", ".join(repr(ch) for ch in unsupported)
        raise CreamTextError(f"지원하지 않는 문자: {shown} (폰트 {font_name}에 없음)")
    return [line for line in result if line]


def _shift(strokes: list[Stroke], dx: float, dy: float) -> list[Stroke]:
    return [[(x + dx, y + dy) for x, y in stroke] for stroke in strokes]


def _ink_mid(strokes: list[Stroke], index: int) -> float:
    values = [p[index] for stroke in strokes for p in stroke]
    return (min(values) + max(values)) / 2 if values else 0.0


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


def text_room(job: Job) -> float:
    """획 중심선이 닿을 수 있는 반경."""
    return text_limit_radius(job) - job.line_width / 2


def _min_char_height(job: Job) -> float:
    return MIN_CHAR_HEIGHT_PER_LINE_WIDTH * job.line_width


def _too_long(fit: float, job: Job) -> CreamTextError:
    return CreamTextError(f"문구가 너무 김: 가능한 글자 높이 {fit:.1f} mm < 최소 {_min_char_height(job):.1f} mm")


def outside_cake(fit: float, subject: str = "글씨가", size: str = "글자 높이") -> CreamTextError:
    return CreamTextError(f"{subject} 케이크 반경 밖: {size} 최대 {max(fit, 0):.1f} mm")


def _check_char_height(job: Job) -> None:
    if job.char_height is not None and job.char_height < _min_char_height(job):
        raise CreamTextError(f"글자 높이 {job.char_height:g} mm < 최소 {_min_char_height(job):.1f} mm(선 폭 기준)")


@dataclass(frozen=True)
class _Placed:
    ch: str
    glyph: GlyphLike
    strokes: list[Stroke]


def _all_strokes(placed: list[_Placed]) -> list[Stroke]:
    return [s for p in placed for s in p.strokes]


def _place_font(lines: list[str], font: FontLike, height: float, job: Job, opts: FontOptions) -> list[_Placed]:
    """em 높이 height로 배치함. 글자 간격은 폰트 advance. 줄마다 가로, 전체를 세로로 획 기준 중심 정렬."""
    placed: list[_Placed] = []
    for row, line in enumerate(lines):
        x, y0 = 0.0, -row * FONT_LINE_PITCH_EM * height
        row_items = []
        for ch in line:
            glyph = font.glyph_paths(ch, height, job.line_width, opts.style, opts.fallback)
            row_items.append(_Placed(ch, glyph, _shift(glyph.strokes, x, y0)))
            x += glyph.advance
        dx = -_ink_mid(_all_strokes(row_items), 0)
        placed += [_Placed(p.ch, p.glyph, _shift(p.strokes, dx, 0.0)) for p in row_items]
    dy = -_ink_mid(_all_strokes(placed), 1)
    return [_Placed(p.ch, p.glyph, _shift(p.strokes, 0.0, dy)) for p in placed]


def _ceil_tenth(value: float) -> float:
    return math.ceil(value * 10 - 1e-9) / 10


def _thin_error(thin: list[_Placed], height: float, max_height: float) -> CreamTextError:
    needs = {p.ch: p.glyph.min_char_height for p in thin}
    shown = ", ".join(f"{ch!r} {_ceil_tenth(need):g} mm 이상" if need else f"{ch!r} 해결 불가"
                      for ch, need in needs.items())
    msg = f"선 폭보다 가는 글자: {shown} (현재 글자 높이 {height:.1f} mm)"
    if all(needs.values()):
        need = _ceil_tenth(max(needs.values()))
        if need <= max_height:
            msg += f". --char-height {need:g} 제안"
        else:
            msg += f". 케이크 안 최대 글자 높이 {max_height:.1f} mm로 부족"
    return CreamTextError(msg + ". 굵은 --weight, 좁은 --line-width, --allow-thin(건너뜀) 검토")


def add_strokes(layout: TextLayout, glyph: GlyphLike, strokes: list[Stroke], style: str) -> bool:
    """획을 짤 목록에 더하고 폴백 표시와 집계를 함. 폴백으로 그렸으면 True."""
    is_fallback = glyph.method not in (style, NO_STROKE_METHOD)
    if is_fallback:
        layout.fallback_counts[glyph.method] = layout.fallback_counts.get(glyph.method, 0) + 1
    for stroke in strokes:
        if len(stroke) < 2:
            continue
        if is_fallback:
            layout.fallback.add(len(layout.strokes))
        layout.strokes.append(stroke)
    return is_fallback


def _font_layout(lines: list[str], placed: list[_Placed], opts: FontOptions, height: float,
                 auto_height: bool) -> TextLayout:
    layout = TextLayout(lines, [], char_height=height, auto_height=auto_height)
    for p in placed:
        if p.glyph.too_thin:
            layout.skipped.append(p.ch)
            continue
        need = p.glyph.min_char_height
        if need is not None:
            layout.readable_height = max(need, layout.readable_height or 0.0)
        if add_strokes(layout, p.glyph, p.strokes, opts.style) and need is None:
            layout.unrated.append(p.ch)
    return layout


def font_text(job: Job, font: FontLike, opts: FontOptions) -> TextLayout:
    """폰트 윤곽으로 글씨 획을 만듦. 크기, 범위는 실제 경로 좌표로 맞춤.

    폰트 경로는 글자 높이에 비례하지 않으므로 자동 크기는 줄여 가며 다시 배치함.
    """
    lines = validate_lines(job.lines, font, opts.spec)
    _check_char_height(job)
    room = text_room(job)
    height = AUTO_CHAR_HEIGHT_CAP if job.char_height is None else job.char_height
    for _ in range(FIT_TRIES):
        placed = _place_font(lines, font, height, job, opts)
        reach = max_radius(_all_strokes(placed))
        if reach <= room:
            break
        fit = height * max(room, 0.0) / reach if reach > 0 else 0.0
        if job.char_height is not None:
            raise outside_cake(fit)
        if fit < _min_char_height(job):
            raise _too_long(fit, job)
        height = max(fit * FIT_SHRINK, _min_char_height(job))
    else:
        raise CreamTextError("글자 크기 맞춤 실패: --char-height 지정")
    thin = [p for p in placed if p.glyph.too_thin]
    if thin and not opts.allow_thin:
        max_height = height * room / reach if reach > 0 else math.inf
        raise _thin_error(thin, height, max_height)
    return _font_layout(lines, placed, opts, height, job.char_height is None)


def fallback_summary(counts: dict[str, int], unit: str = "자") -> str:
    methods = [m for m in FALLBACK_ORDER if m in counts] + sorted(set(counts) - set(FALLBACK_ORDER))
    return "폴백: " + ", ".join(f"{m} {counts[m]}{unit}" for m in methods)


def small_text_warning(layout: TextLayout) -> str:
    need = _ceil_tenth(layout.readable_height or 0.0)
    return (f"경고: 글자가 작아 읽기 어려울 수 있음(글자 높이 {layout.char_height:.1f} mm < 권장 {need:g} mm).\n"
            "    해결: 줄 나누기(문구를 인자로 분리), --margin, --border-inset, --flower-inset 줄이기, "
            "--char-height 검토")


def unrated_warning(layout: TextLayout) -> str:
    shown = ", ".join(repr(ch) for ch in dict.fromkeys(layout.unrated))
    return (f"경고: 폴백으로 그린 글자의 권장 높이 계산 불가: {shown}.\n"
            "    해결: --preview로 확인, --char-height 키우기 또는 굵은 --weight 검토")


@contextmanager
def as_cream_error(errors: tuple[type[Exception], ...]) -> Iterator[None]:
    try:
        yield
    except errors as exc:
        raise CreamTextError(str(exc)) from exc


class _CheckedFont:
    """폰트 모듈의 사용자 오류를 CreamTextError로 바꿈."""

    def __init__(self, font: FontLike, errors: tuple[type[Exception], ...]) -> None:
        self._font = font
        self._errors = errors

    def has_glyph(self, ch: str) -> bool:
        with as_cream_error(self._errors):
            return self._font.has_glyph(ch)

    def glyph_paths(self, ch: str, char_height: float, line_width: float, style: str = "fill",
                    fallback: str = "auto") -> GlyphLike:
        with as_cream_error(self._errors):
            return self._font.glyph_paths(ch, char_height, line_width, style, fallback)


def open_font(opts: FontOptions) -> FontLike:
    """의존 모듈은 실행 시점에 불러옴. 모듈 import만으로는 의존이 필요 없게 함."""
    try:
        import fontglyphs
        import fontsrc
    except ImportError as exc:
        raise CreamTextError(f"폰트 처리에 필요한 모듈 없음({exc.name}). {VENV_HINT}") from exc
    errors = (fontsrc.FontError, fontglyphs.FontGlyphError)
    with as_cream_error(errors):
        font = fontglyphs.OutlineFont(fontsrc.resolve_font(opts.spec), opts.weight)
    return _CheckedFont(font, errors)


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
        if not 0 <= tool < self.machine.tool_count:
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


def _check_ranges(job: Job) -> None:
    for name in ("cake_diameter", "height", "gap", "lift", "line_width", "line_height", "margin", "border_inset",
                 "flower_inset", "flower_ml", "char_height"):
        value = getattr(job, name)
        if value is not None and not math.isfinite(value):
            raise CreamTextError(f"{name}는 유한한 수여야 함")
    for name in ("cake_diameter", "height", "line_width", "line_height", "char_height"):
        value = getattr(job, name)
        if value is not None and value <= 0:
            raise CreamTextError(f"{name}는 0보다 커야 함")
    for name in ("gap", "lift", "margin", "border_inset", "flower_inset"):
        if getattr(job, name) < 0:
            raise CreamTextError(f"{name}는 0 이상이어야 함")


def check_job(machine: Machine, job: Job) -> None:
    if job.bead_area > machine.max_cross_section:
        raise CreamTextError(
            f"선 단면 {job.bead_area:.2f} mm² > max_extrude_cross_section {machine.max_cross_section:.2f} mm²")
    _check_ranges(job)
    for name, used in (("margin", True), ("border_inset", job.border), ("flower_inset", bool(job.flowers))):
        if used and getattr(job, name) >= job.cake_radius:
            raise CreamTextError(f"{name} {getattr(job, name):g} mm >= 케이크 반경 {job.cake_radius:g} mm")
    if job.flowers < 0 or (job.flowers and job.flower_ml <= 0):
        raise CreamTextError("flowers, flower_ml 값 오류")


def _ascii_comment(text: str) -> str:
    """Klipper, Mainsail이 gcode를 ASCII로 가정할 수 있어 비ASCII는 \\uXXXX로 씀."""
    return text.encode("ascii", "backslashreplace").decode("ascii")


def build_gcode(machine: Machine, job: Job, layout: TextLayout,
                tool_name: str = "cream_text") -> tuple[str, dict[int, float]]:
    """gcode와 카트리지별 짜는 크림 부피(mL, PRIME 제외)를 반환함."""
    check_job(machine, job)
    layout.check()
    write_z = job.height + job.gap
    travel_z = write_z + job.lift

    w = GcodeWriter(machine)
    w.emit(f"; printc {tool_name}: {_ascii_comment(' / '.join(layout.lines))}")
    w.emit("G28")
    w.emit("G90")
    w.emit("M83")
    w.set_tool(layout.tool_for(0))
    for index, stroke in enumerate(layout.strokes):
        if layout.tool_for(index) != w.tool:
            w.set_tool(layout.tool_for(index))
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


# ---------- 미리 보기 ----------

def _svg_points(stroke: Stroke) -> str:
    # SVG는 y가 아래쪽 양수이므로 뒤집음.
    return " ".join(f"{x:.2f},{-y:.2f}" for x, y in stroke)


def preview_svg(job: Job, layout: TextLayout) -> str:
    """짜는 경로를 케이크 중심 기준 SVG로 그림. 이동은 점선.

    글씨는 폴백 획의 색을 달리함. 획마다 색이 있으면(그림) 실제 색을 쓰고, 밝은 색도 보이게 케이크 바탕을
    크림색으로 채우고 획 밑에 어두운 테두리를 깔며, 폴백 획은 위에 주황 점선을 얹음.
    """
    layout.check()
    colored = bool(layout.colors)
    base = layout.colors or [SVG_COLORS["text"]] * len(layout.strokes)
    paths = [(base[i] if colored or i not in layout.fallback else SVG_COLORS["fallback"], s)
             for i, s in enumerate(layout.strokes)]
    marks = [layout.strokes[i] for i in sorted(layout.fallback)] if colored else []
    if job.border:
        paths.append((SVG_COLORS["border"], border_stroke(job)))
    dots = flower_points(job) if job.flowers else []
    order = [s for _, s in paths] + [[p] for p in dots]
    travel = " ".join(f"M{a[-1][0]:.2f},{-a[-1][1]:.2f} L{b[0][0]:.2f},{-b[0][1]:.2f}"
                      for a, b in zip(order, order[1:]))
    r = job.cake_radius
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-r:g} {-r:g} {2 * r:g} {2 * r:g}" '
        f'width="{2 * r:g}mm" height="{2 * r:g}mm">',
        f'<circle r="{r:g}" fill="{SVG_COLORS["paper"] if colored else "none"}" stroke="{SVG_COLORS["cake"]}" '
        'stroke-width="0.5"/>',
        *([f'<g fill="none" stroke="{SVG_COLORS["outline"]}" stroke-opacity="0.3" '
           f'stroke-width="{job.line_width + 1.2:g}" stroke-linecap="round" stroke-linejoin="round">',
           *(f'<polyline points="{_svg_points(s)}"/>' for _, s in paths), "</g>"] if colored else []),
        f'<g fill="none" stroke-width="{job.line_width:g}" stroke-linecap="round" stroke-linejoin="round" '
        f'opacity="{1 if colored else 0.8}">',
        *(f'<polyline stroke="{color}" points="{_svg_points(s)}"/>' for color, s in paths),
        "</g>",
        *([f'<g fill="none" stroke="{SVG_COLORS["fallback"]}" stroke-width="0.6" stroke-dasharray="1.5 1.5">',
           *(f'<polyline points="{_svg_points(s)}"/>' for s in marks), "</g>"] if marks else []),
        *(f'<circle cx="{x:.2f}" cy="{-y:.2f}" r="{job.line_width:g}" fill="{SVG_COLORS["flower"]}"/>'
          for x, y in dots),
    ]
    if travel:
        out.append(f'<path d="{travel}" fill="none" stroke="{SVG_COLORS["travel"]}" stroke-width="0.3" '
                   'stroke-dasharray="1 1"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ---------- CLI ----------

def resolve_inside(path: Path, root: Path, what: str) -> Path:
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root):
        raise CreamTextError(f"{what} 경로가 저장소 밖: {path}")
    return resolved


def resolve_output(path: Path, root: Path, flag: str, suffix: str) -> Path:
    out = resolve_inside(path, root, flag)
    if out.suffix != suffix:
        raise CreamTextError(f"{flag} 확장자는 {suffix}")
    if out.is_dir():
        raise CreamTextError(f"{flag}이 디렉터리: {out}")
    return out


def add_job_args(p: argparse.ArgumentParser) -> None:
    """글씨, 그림 공통 인자: 출력, 케이크, 크림 선, 테두리, 꽃, 위치, 미리 보기."""
    p.add_argument("--out", type=Path, required=True, help="출력 .gcode (저장소 안)")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE,
                   help="프로필 디렉터리 (저장소 안). 기본 %(default)s")
    p.add_argument("--cake-diameter", type=float, default=DEFAULT_CAKE_DIAMETER,
                   help="케이크 지름 mm. 기본 %(default)s(3호)")
    p.add_argument("--height", type=float, default=DEFAULT_CAKE_HEIGHT,
                   help="케이크 높이 mm (판 윗면 기준). 기본 %(default)s(3호)")
    p.add_argument("--gap", type=float, help="케이크 윗면에서 깍지 끝까지 mm. 생략 시 _CREAM_CFG gap")
    p.add_argument("--lift", type=float, default=5.0, help="획 사이 이동 시 추가 높이 mm. 기본 %(default)s")
    p.add_argument("--line-width", type=float, help="크림 선 폭 mm. 생략 시 _CREAM_CFG line_width")
    p.add_argument("--line-height", type=float, help="크림 선 높이 mm. 생략 시 _CREAM_CFG line_height")
    p.add_argument("--margin", type=float, default=10.0,
                   help="케이크 가장자리에서 글씨까지 최소 거리 mm. 기본 %(default)s")
    p.add_argument("--border", action="store_true", help=f"T{BORDER_TOOL} 테두리 추가")
    p.add_argument("--border-inset", type=float, default=8.0, help="가장자리에서 테두리까지 mm. 기본 %(default)s")
    p.add_argument("--flowers", type=int, default=0, help=f"T{FLOWER_TOOL} 꽃 개수. 기본 %(default)s")
    p.add_argument("--flower-inset", type=float, default=20.0, help="가장자리에서 꽃 중심까지 mm. 기본 %(default)s")
    p.add_argument("--flower-ml", type=float, default=0.5, help="꽃 하나 크림 mL. 기본 %(default)s")
    p.add_argument("--center", type=float, nargs=2, metavar=("X", "Y"),
                   help="케이크 중심. 생략 시 _CREAM_CFG center_x, center_y")
    p.add_argument("--preview", type=Path, help="경로 미리 보기 .svg (저장소 안)")


def add_stroke_args(p: argparse.ArgumentParser, allow_thin: bool = True) -> None:
    """채움, 가는 획 처리 인자. allow_thin이면 폴백으로도 못 그리는 글자를 건너뛰는 --allow-thin도 받음."""
    p.add_argument("--style", choices=FONT_STYLES, default=FONT_STYLES[0], help="채움 방식. 기본 %(default)s")
    p.add_argument("--fallback", choices=FONT_FALLBACKS, default=FONT_FALLBACKS[0],
                   help="가는 획 처리. 기본 %(default)s")
    if allow_thin:
        p.add_argument("--allow-thin", action="store_true", help="폴백으로도 못 그리는 글자를 건너뜀")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="문구를 생크림 레터링 gcode로 변환함. 인자 하나가 한 줄.")
    p.add_argument("text", nargs="+", help="문구 줄. 폰트에 있는 글자만")
    add_job_args(p)
    p.add_argument("--char-height", type=float, help="글자 em 높이 mm. 생략 시 맞춤")
    p.add_argument("--font", help=f"폰트 패밀리 이름 또는 .ttf/.otf 경로. 기본 {DEFAULT_FONT}")
    weight_help = ("생략 시 폰트 기본 굵기" if DEFAULT_WEIGHT is None
                   else f"생략 시 기본 폰트는 {DEFAULT_WEIGHT:g}, 그 외는 폰트 기본 굵기")
    p.add_argument("--weight", type=float, help=f"가변 폰트 굵기(wght). {weight_help}")
    add_stroke_args(p)
    return p.parse_args(argv)


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


def font_options(args: argparse.Namespace) -> FontOptions:
    """DEFAULT_WEIGHT는 기본 폰트에만 적용. 다른 폰트는 --weight 생략 시 폰트 기본 굵기."""
    weight = args.weight
    if weight is None and args.font is None:
        weight = DEFAULT_WEIGHT
    return FontOptions(spec=args.font or DEFAULT_FONT, style=args.style, weight=weight,
                       fallback=args.fallback, allow_thin=args.allow_thin)


def make_job(args: argparse.Namespace, machine: Machine, lines: list[str], char_height: float | None) -> Job:
    """공통 인자와 기계 기본값으로 작업을 만듦. char_height는 글자 높이나 그림 크기."""
    return Job(
        lines=lines, cake_diameter=args.cake_diameter, height=args.height,
        gap=_or(args.gap, machine.gap), lift=args.lift, char_height=char_height,
        line_width=_or(args.line_width, machine.line_width),
        line_height=_or(args.line_height, machine.line_height), margin=args.margin, border=args.border,
        border_inset=args.border_inset, flowers=args.flowers, flower_inset=args.flower_inset,
        flower_ml=args.flower_ml,
    )


def write_files(files: list[tuple[Path, str]]) -> None:
    """여러 파일을 한꺼번에 씀. 같은 폴더의 임시 파일에 모두 쓴 뒤 바꿔치므로, 쓰다 실패하면 기존 파일을 건드리지 않음.

    임시 파일 이름은 매번 새로 만들어 사용자 파일, 심링크와 겹치지 않음.
    """
    staged: list[tuple[Path, Path]] = []
    try:
        for path, text in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
            os.close(fd)
            tmp = Path(name)
            staged.append((tmp, path))
            tmp.write_text(text, encoding="utf-8")
            tmp.chmod(0o644)
        for tmp, path in staged:
            tmp.replace(path)
    finally:
        for tmp, _ in staged:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)


def gcode_summary(volumes: dict[int, float], out: Path, root: Path, note: str = "") -> str:
    """생성 메시지 한 줄. note는 크림 부피 앞에 붙일 설명."""
    summary = ", ".join(f"T{t} {v:.1f} mL" for t, v in sorted(volumes.items()))
    prefix = f"{note}, " if note else ""
    return f"생성: {out.relative_to(root)} ({prefix}짜는 크림 {summary}, PRIME 제외)"


def preview_summary(preview: Path, root: Path) -> str:
    return f"미리 보기: {preview.relative_to(root)}"


def write_outputs(machine: Machine, job: Job, layout: TextLayout, out: Path, preview: Path | None,
                  root: Path, detail: str) -> str:
    """gcode와 미리 보기를 쓰고 완료 메시지를 반환함. detail은 생성 줄 다음에 넣을 줄."""
    gcode, volumes = build_gcode(machine, job, layout)
    files = [(out, gcode)] + ([(preview, preview_svg(job, layout))] if preview else [])
    write_files(files)
    lines = [gcode_summary(volumes, out, root), detail]
    if preview:
        lines.append(preview_summary(preview, root))
    return "\n".join(lines)


def _generate(args: argparse.Namespace, root: Path, font_factory: FontFactory) -> tuple[Path, str, TextLayout]:
    profile = resolve_inside(args.profile, root, "--profile")
    out = resolve_output(args.out, root, "--out", ".gcode")
    preview = resolve_output(args.preview, root, "--preview", ".svg") if args.preview else None
    opts = font_options(args)
    machine = load_machine(profile, tuple(args.center) if args.center else None)
    job = make_job(args, machine, args.text, args.char_height)
    # 폰트를 열기(내려받기) 전에 작업 값 오류를 알림. build_gcode는 단독 호출 대비로 다시 확인함.
    check_job(machine, job)
    layout = font_text(job, font_factory(opts), opts)
    detail = f"글자 높이 {layout.char_height:.1f} mm({'자동' if layout.auto_height else '지정'})"
    return out, write_outputs(machine, job, layout, out, preview, root, detail), layout


def main(argv: list[str] | None = None, root: Path = REPO_ROOT, font_factory: FontFactory = open_font) -> int:
    args = parse_args(argv)
    try:
        _, message, layout = _generate(args, root.resolve(), font_factory)
    except (CreamTextError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(message)
    if layout.fallback_counts:
        print(fallback_summary(layout.fallback_counts))
    if layout.skipped:
        shown = ", ".join(repr(ch) for ch in dict.fromkeys(layout.skipped))
        print(f"경고: 선 폭보다 가는 글자 건너뜀: {shown}", file=sys.stderr)
    if layout.too_small:
        print(small_text_warning(layout), file=sys.stderr)
    if layout.unrated:
        print(unrated_warning(layout), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
