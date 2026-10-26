#!/usr/bin/env python3
"""이미지 파일을 색별 생크림 그림 gcode로 변환함.

이미지를 일러스트처럼 단순화하고 색 수만큼 색을 나눠, 색마다 윤곽을 채워 그림.
기계 값은 프로필 cfg에서, 작업 값은 인자로 받음.
선 폭, 선 높이, 깍지 간격은 cfg 값을 쓰고 인자로 덮어쓸 수 있음. 사용법은 docs/drawing.md.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import cream_text as ct

# 그림 긴 변의 하한(선 폭 배수). 이보다 작으면 형태를 알아볼 수 없음.
MIN_SIZE_LINE_WIDTHS = 5.0
DEFAULT_COLORS = 3
# 색마다 gcode 하나(split) 또는 카트리지 T0, T1, …로 전환하는 gcode 하나(single).
FILE_MODES = ("split", "single")
TOOL_NAME = "cream_image"


@dataclass(frozen=True)
class ImageOptions:
    path: Path
    colors: int = DEFAULT_COLORS
    simplify: bool = True
    draw_background: bool = False
    style: str = "fill"
    fallback: str = "auto"
    allow_thin: bool = False


class LayerLike(Protocol):
    hex: str


class RegionLike(Protocol):
    """imageregion.ImageRegions 계약. layers는 짜는 순서."""

    layers: list[LayerLike]
    side: float
    reach: float

    def parts(self, index: int, size: float) -> list[Any]: ...


TraceFn = Callable[[Any, float, float, str, str], ct.GlyphLike]


@dataclass(frozen=True)
class Backend:
    """의존 모듈 묶음. 시험에서 바꿔 끼움."""

    load_regions: Callable[[ImageOptions], RegionLike]
    trace_region: TraceFn


@dataclass(frozen=True)
class Layer:
    """한 색의 획. layout.colors는 모두 color."""

    color: str
    layout: ct.TextLayout


def open_backend() -> Backend:
    """의존 모듈은 실행 시점에 불러옴. 모듈 import만으로는 의존이 필요 없게 함."""
    try:
        import fontglyphs
        import imageregion
        import shapely.errors
    except ImportError as exc:
        raise ct.CreamTextError(f"이미지 처리에 필요한 모듈 없음({exc.name}). {ct.VENV_HINT}") from exc
    errors = (imageregion.ImageRegionError, fontglyphs.FontGlyphError, shapely.errors.ShapelyError)

    def load_regions(opts: ImageOptions) -> RegionLike:
        with ct.as_cream_error(errors):
            return imageregion.load_regions(opts.path, opts.colors, opts.simplify, opts.draw_background)

    def trace_region(part: Any, ref: float, line_width: float, style: str, fallback: str) -> ct.GlyphLike:
        with ct.as_cream_error(errors):
            return fontglyphs.trace_region(part, ref, line_width, style, fallback)

    return Backend(load_regions, trace_region)


def _label(path: Path) -> str:
    """gcode 머리 주석에 쓸 파일 이름. 줄바꿈 등 제어 문자가 주석 밖으로 새지 않게 함."""
    return "".join(ch if ch.isprintable() else "?" for ch in path.name)


def _min_size(job: ct.Job) -> float:
    return MIN_SIZE_LINE_WIDTHS * job.line_width


def _thin_error(count: int, size: float, auto: bool) -> ct.CreamTextError:
    grow = "" if auto else "--size 키우기, "
    return ct.CreamTextError(f"선 폭보다 가는 조각 {count}개 (현재 크기 {size:.1f} mm). "
                             f"{grow}좁은 --line-width, 적은 --colors, --allow-thin(건너뜀) 검토")


def _layer(label: str, color: str, traced: list[ct.GlyphLike], opts: ImageOptions, size: float,
           auto: bool) -> Layer | None:
    """한 색의 획. 그릴 획이 없으면 None."""
    layout = ct.TextLayout([f"{label} {color}"], [], char_height=size, auto_height=auto)
    for index, glyph in enumerate(traced):
        if glyph.too_thin:
            layout.skipped.append(f"#{index + 1}")
            continue
        ct.add_strokes(layout, glyph, glyph.strokes, opts.style)
    layout.colors = [color] * len(layout.strokes)
    return Layer(color, layout) if layout.strokes else None


def image_layers(job: ct.Job, source: RegionLike, opts: ImageOptions, trace: TraceFn) -> list[Layer]:
    """색별 획을 만듦. job.char_height가 그림 긴 변(mm), 생략하면 케이크 안 최대. 색은 source 순서.

    획은 윤곽 안쪽에 그려지므로 윤곽이 들어가면 획도 들어감. 중심선 폴백이 윤곽을 살짝 넘을 수 있어
    실제 경로 좌표로 다시 확인하고, 자동 크기는 줄여 가며 다시 그림. 크기는 모든 색이 같음.
    """
    room = ct.text_room(job)
    auto = job.char_height is None
    size = source.side * ct.text_limit_radius(job) / source.reach if auto else job.char_height
    for _ in range(ct.FIT_TRIES):
        if size < _min_size(job):
            raise ct.CreamTextError(f"그림 크기 {size:.1f} mm < 최소 {_min_size(job):.1f} mm(선 폭 기준)")
        # 윤곽이 이미 케이크 밖이면 획을 만들지 않고 알림. 중심선 폴백이 넘을 수 있는 선 폭만큼은 봐줌.
        if not auto and size * source.reach / source.side > ct.text_limit_radius(job) + job.line_width:
            raise ct.outside_cake(source.side * room / source.reach, "그림이", "그림 크기")
        traced = [[trace(part, size, job.line_width, opts.style, opts.fallback) for part in source.parts(i, size)]
                  for i in range(len(source.layers))]
        reach = ct.max_radius([s for glyphs in traced for g in glyphs if not g.too_thin for s in g.strokes])
        if reach <= room:
            break
        fit = size * max(room, 0.0) / reach
        if not auto:
            raise ct.outside_cake(fit, "그림이", "그림 크기")
        size = fit * ct.FIT_SHRINK
    else:
        raise ct.CreamTextError("그림 크기 맞춤 실패: --size 지정")
    thin = sum(g.too_thin for glyphs in traced for g in glyphs)
    if thin and not opts.allow_thin:
        raise _thin_error(thin, size, auto)
    label = _label(opts.path)
    layers = [_layer(label, source.layers[i].hex, glyphs, opts, size, auto) for i, glyphs in enumerate(traced)]
    layers = [layer for layer in layers if layer]
    if not layers:
        raise ct.CreamTextError("짤 획이 없음: --size 키우기, 좁은 --line-width 검토")
    return layers


def combine(label: str, layers: list[Layer], per_tool: bool) -> ct.TextLayout:
    """색별 획을 한 레이아웃으로 합침. per_tool이면 색 순서대로 T0, T1, …로 전환."""
    first = layers[0].layout
    lines = [label] + ([f"T{tool} {layer.color}" for tool, layer in enumerate(layers)] if per_tool else [])
    merged = ct.TextLayout(lines, [], char_height=first.char_height, auto_height=first.auto_height)
    for tool, layer in enumerate(layers):
        part = layer.layout
        merged.fallback |= {len(merged.strokes) + i for i in part.fallback}
        merged.strokes += part.strokes
        merged.colors += part.colors
        merged.tools += [tool] * len(part.strokes) if per_tool else []
        merged.skipped += part.skipped
        for method, count in part.fallback_counts.items():
            merged.fallback_counts[method] = merged.fallback_counts.get(method, 0) + count
    return merged


def numbered(path: Path, index: int, count: int) -> Path:
    """색이 둘 이상이면 파일 이름에 순번을 붙임. 예: job.gcode → job_1.gcode."""
    return path if count == 1 else path.with_name(f"{path.stem}_{index}{path.suffix}")


def _stale(out: Path, written: list[Path]) -> list[str]:
    """이번에 쓰지 않은 같은 이름의 gcode(--out 그대로, 순번 붙은 것). 오래된 파일을 실행하지 않게 알림."""
    pattern = re.compile(rf"^{re.escape(out.stem)}(_\d+)?{re.escape(out.suffix)}$")
    return sorted(p.name for p in out.parent.glob(f"{out.stem}*{out.suffix}")
                  if p not in written and pattern.match(p.name))


def _colors_type(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("1 이상")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="이미지 파일을 색별 생크림 그림 gcode로 변환함.")
    p.add_argument("image", type=Path, help="이미지 파일 (PNG, JPEG, BMP, GIF, WEBP)")
    ct.add_job_args(p)
    p.add_argument("--size", type=float, help="그림 긴 변 mm. 생략 시 케이크 안 최대")
    p.add_argument("--colors", type=_colors_type, default=DEFAULT_COLORS,
                   help="그릴 색 수(배경 제외). 기본 %(default)s")
    p.add_argument("--files", choices=FILE_MODES, default=FILE_MODES[0],
                   help="split: 색마다 gcode 하나(_1, _2, …), 카트리지를 갈아 끼워 차례로 실행. "
                        f"single: gcode 하나에 T0, T1, …로 전환. 기본 %(default)s")
    p.add_argument("--simplify", action=argparse.BooleanOptionalAction, default=True,
                   help="일러스트처럼 단순화(질감, 잔 얼룩 제거)한 뒤 색을 나눔. 기본 켬")
    p.add_argument("--draw-background", action="store_true", help="테두리와 가장 많이 닿은 배경색도 그림")
    ct.add_stroke_args(p, "조각")
    return p.parse_args(argv)


def _generate(args: argparse.Namespace, root: Path, backend: Backend) -> tuple[str, ct.TextLayout]:
    profile = ct.resolve_inside(args.profile, root, "--profile")
    out = ct.resolve_output(args.out, root, "--out", ".gcode")
    preview = ct.resolve_output(args.preview, root, "--preview", ".svg") if args.preview else None
    opts = ImageOptions(args.image, args.colors, args.simplify, args.draw_background, args.style, args.fallback,
                        args.allow_thin)
    machine = ct.load_machine(profile, tuple(args.center) if args.center else None)
    if args.size is not None and not (math.isfinite(args.size) and args.size > 0):
        raise ct.CreamTextError("--size는 0보다 큰 유한한 수여야 함")
    if args.files == "single" and args.colors > machine.tool_count:
        raise ct.CreamTextError(f"--colors {args.colors} > 카트리지 {machine.tool_count}개: --files split 사용")
    label = _label(args.image)
    job = ct.make_job(args, machine, [label], args.size)
    # 이미지를 읽기 전에 작업 값 오류를 알림. build_gcode는 단독 호출 대비로 다시 확인함.
    ct.check_job(machine, job)
    layers = image_layers(job, backend.load_regions(opts), opts, backend.trace_region)
    if len(layers) > 1 and (job.border or job.flowers):
        raise ct.CreamTextError("색이 둘 이상이면 --border, --flowers 사용 불가(T1, T2를 씀): --colors 1 사용")
    single = args.files == "single"
    merged = combine(label, layers, per_tool=single)
    # 하나라도 실패하면 아무것도 쓰지 않도록 경로 검증과 gcode 생성을 모두 마친 뒤 씀.
    if single:
        jobs = [(out, merged, ", ".join(f"T{t} {layer.color}" for t, layer in enumerate(layers)))]
    else:
        jobs = [(ct.resolve_output(numbered(out, i, len(layers)), root, "--out", ".gcode"), layer.layout, layer.color)
                for i, layer in enumerate(layers, 1)]
    built = [(path, *ct.build_gcode(machine, job, layout, TOOL_NAME), note) for path, layout, note in jobs]
    lines = [ct.save_gcode(gcode, volumes, path, root, note) for path, gcode, volumes, note in built]
    lines += [f"경고: 이전 실행의 파일이 남아 있음(실행 금지): {name}" for name in _stale(out, [b[0] for b in built])]
    lines.append(f"그림 크기 {merged.char_height:.1f} mm({'자동' if merged.auto_height else '지정'})")
    if len(layers) < args.colors:
        lines.append(f"색 {len(layers)}개만 남음(요청 {args.colors}개): 비슷한 색은 합치고 작은 색은 뺌")
    if preview:
        lines.append(ct.write_preview(job, merged, preview, root))
    return "\n".join(lines), merged


def main(argv: list[str] | None = None, root: Path = ct.REPO_ROOT, backend: Backend | None = None) -> int:
    args = parse_args(argv)
    try:
        message, layout = _generate(args, root.resolve(), backend or open_backend())
    except (ct.CreamTextError, OSError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(message)
    if layout.fallback_counts:
        print(ct.fallback_summary(layout.fallback_counts, "조각"))
    if layout.skipped:
        print(f"경고: 선 폭보다 가는 조각 {len(layout.skipped)}개 건너뜀", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
