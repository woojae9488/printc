"""폰트 윤곽선을 크림 압출 경로로 바꿈. 영역을 경로로 바꾸는 trace_region은 이미지 윤곽에도 씀.

좌표는 mm, 원점은 글자 왼쪽 기준선 위, y 위쪽 양수. 크기는 em 높이(char_height) 기준.
획이 선 폭보다 얇으면 굵히기(embolden), 중심선(center) 순으로 폴백함.
"""

from __future__ import annotations

import io
import itertools
import math
import struct
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import shapely
import shapely.errors
from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont, TTLibError
from fontTools.varLib import instancer
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

Point = tuple[float, float]
Stroke = list[Point]

STYLES = ("fill", "outline")
# 폴백 이름별 시도 단계.
FALLBACKS = {"auto": ("embolden", "center"), "none": (), "embolden": ("embolden",), "center": ("center",)}
FLATTEN_TOLERANCE_MM = 0.1
# 폰트에 공백 글리프가 없을 때 공백 폭(em 비율).
DEFAULT_SPACE_EM = 0.25
# 굵기 부족 판정. 덮이지 않은 조각이 선 폭² 보다 넓거나, 덮인 부분과 떨어졌거나(획이 통째로 빠짐),
# 글자 골격을 이 길이보다 길게 못 덮으면 부족. 면적만 보면 선 폭이 글자에 비해 클 때 획 하나가 통째로 빠져도 통과함.
THIN_LOST_AREA_LINE_WIDTHS_SQ = 1.0
# 글자 크기가 선 폭의 수 배면 위 절대 기준 안의 조각 여럿으로 가로획이 다 빠져도 통과하므로 원 영역 대비 상한도 둠.
# 실제 폰트에서 정상 글자는 총 7.4%, 조각 6.5% 이하, 가로획이 빠진 글자는 총 19%, 조각 10% 이상이라 그 사이로 둠.
THIN_LOST_TOTAL_RATIO = 0.10
THIN_LOST_PIECE_RATIO = 0.08
# 예각 모서리(45° 근처)와 붓 획 끝은 가지치기 뒤에도 골격을 선 폭 0.9–1.4배 못 덮으므로 그보다 크게 둠.
THIN_LOST_SKELETON_LINE_WIDTHS = 1.5
# 덮인 부분에서 이 거리 안에 다 들어오는 조각은 둔각 모서리 자투리로 보고 골격 검사를 생략함.
THIN_CORNER_REACH_LINE_WIDTHS = 0.5
# 원 영역 조각마다 덮인 부분이 하나로 이어져야 함. 이 폭 이하 틈은 크림이 퍼져 이어짐.
THIN_JOIN_GAP_LINE_WIDTHS = 0.1
COLLECTION_TAG = b"ttcf"
WEIGHT_AXIS = "wght"
# 손상되거나 악의적인 폰트 방어. unitsPerEm 은 OpenType 명세 범위, 점·윤곽 수는 곡선을 편 뒤 기준.
UNITS_PER_EM_RANGE = (16, 16384)
MAX_GLYPH_POINTS = 100_000
MAX_GLYPH_CONTOURS = 1_000

# 굵히기 거리 상한. 글자 크기 비율과 선 폭 비율 중 작은 값. 더 굵히면 글자 모양이 무너짐.
EMBOLDEN_MAX_EM = 0.05
EMBOLDEN_MAX_LINE_WIDTHS = 0.25
EMBOLDEN_SEARCH_STEPS = 6
# 굵힌 뒤 원래 구멍 면적이 이 비율 미만으로 남으면 구멍이 막힌 것으로 봄.
HOLE_KEEP_RATIO = 0.25

# 중심선: 경계 표본 간격은 선 폭 비율과 글자 크기 비율 중 작은 값. 얇은 획 두께보다 촘촘해야 함.
SKELETON_SPACING_LINE_WIDTHS = 0.1
SKELETON_SPACING_EM = 0.005
# 이보다 짧은 끝 가지는 모서리 잔가지로 보고 제거. 이 길이면 잃는 면적이 굵기 부족 기준 미만임.
SKELETON_PRUNE_LINE_WIDTHS = 1.0
# 이보다 짧은 골격 조각은 점으로 보고 버림. 덮인 부분 사이를 잇는 조각은 짧아도 남김.
SKELETON_MIN_LENGTH_LINE_WIDTHS = 0.5
SKELETON_COORD_DIGITS = 9
# 골격 조각 끝이 덮인 부분 경계에 닿았다고 보는 거리(mm).
SKELETON_TOUCH_MM = 1e-6
# 판정용 골격은 조각 주변 이 거리 안만 계산. 가지치기 길이보다 넉넉해야 함.
SKELETON_CLIP_LINE_WIDTHS = 3.0

# min_char_height: em 단위 영역에서 선 폭/글자 높이 비율을 늘려 가며 처음 굵기 부족이 되는 비율을 찾음.
MIN_HEIGHT_FLATTEN_EM = 0.001
MIN_HEIGHT_RATIO_START = 0.005
MIN_HEIGHT_RATIO_MAX = 1.0
MIN_HEIGHT_RATIO_STEP = 1.2
MIN_HEIGHT_SEARCH_STEPS = 6
# 곡선 펴기 오차가 크기마다 달라 경계에서 판정이 뒤집히지 않게 둔 여유.
MIN_HEIGHT_MARGIN = 1.02


class FontGlyphError(Exception):
    """사용자에게 보일 폰트 오류."""


@dataclass(frozen=True)
class GlyphResult:
    strokes: list[Stroke]
    advance: float
    too_thin: bool
    # fill | outline | embolden | center | none(그릴 것 없음)
    method: str = "fill"
    # 폴백 없이 fill 로 온전히 채워지는 최소 char_height(mm). 계산 불가면 None.
    min_char_height: float | None = None


class _FlattenPen(BasePen):
    """곡선을 허용 오차 안의 꺾은선으로 펴 윤곽 목록에 담음."""

    def __init__(self, glyph_set: object, scale: float, tolerance: float = FLATTEN_TOLERANCE_MM) -> None:
        super().__init__(glyph_set)
        self.scale = scale
        self.tolerance = tolerance
        self.contours: list[Stroke] = []
        self._current: Stroke = []
        self._points = 0

    def _grow(self, n: int) -> None:
        self._points += n
        if self._points > MAX_GLYPH_POINTS:
            raise FontGlyphError(f"글리프 점이 {MAX_GLYPH_POINTS}개를 넘음")

    def _point(self, pt: tuple[float, float]) -> Point:
        return pt[0] * self.scale, pt[1] * self.scale

    def _moveTo(self, pt: tuple[float, float]) -> None:
        self._finish()
        self._grow(1)
        self._current = [self._point(pt)]

    def _lineTo(self, pt: tuple[float, float]) -> None:
        self._grow(1)
        self._current.append(self._point(pt))

    def _qCurveToOne(self, pt1: tuple[float, float], pt2: tuple[float, float]) -> None:
        p0, p1, p2 = self._current[-1], self._point(pt1), self._point(pt2)
        bend = math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1])
        n = max(1, math.ceil(math.sqrt(bend / (4 * self.tolerance))))
        self._grow(n)
        for i in range(1, n + 1):
            t = i / n
            a, b, c = (1 - t) ** 2, 2 * t * (1 - t), t * t
            self._current.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))

    def _curveToOne(self, pt1: tuple[float, float], pt2: tuple[float, float], pt3: tuple[float, float]) -> None:
        p0, p1, p2, p3 = self._current[-1], self._point(pt1), self._point(pt2), self._point(pt3)
        bend = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
                   math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
        n = max(1, math.ceil(math.sqrt(3 * bend / (4 * self.tolerance))))
        self._grow(n)
        for i in range(1, n + 1):
            t = i / n
            a, b, c, d = (1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t * t * (1 - t), t ** 3
            self._current.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                                  a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))

    def _closePath(self) -> None:
        self._finish()

    def _endPath(self) -> None:
        self._finish()

    def _finish(self) -> None:
        if len(self._current) >= 3:
            if self._current[0] != self._current[-1]:
                self._current.append(self._current[0])
            self.contours.append(self._current)
            if len(self.contours) > MAX_GLYPH_CONTOURS:
                raise FontGlyphError(f"글리프 윤곽이 {MAX_GLYPH_CONTOURS}개를 넘음")
        self._current = []


def _winding(point: Point, contour: Stroke) -> int:
    x, y = point
    total = 0
    for (x0, y0), (x1, y1) in zip(contour, contour[1:]):
        side = (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0)
        if y0 <= y < y1 and side > 0:
            total += 1
        elif y1 <= y < y0 and side < 0:
            total -= 1
    return total


def _region(contours: list[Stroke]) -> BaseGeometry:
    """윤곽을 nonzero 규칙으로 채운 영역. 윤곽 선으로 나뉜 면마다 감김 수를 셈."""
    if not contours:
        return Polygon()
    lines = shapely.unary_union([LineString(c) for c in contours])
    filled = []
    for face in shapely.get_parts(shapely.polygonize([lines])):
        inside = face.representative_point()
        if sum(_winding((inside.x, inside.y), c) for c in contours):
            filled.append(face)
    return shapely.unary_union(filled) if filled else Polygon()


def _polygons(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        return [g for part in geom.geoms for g in _polygons(part)]
    return []


def _rings(geom: BaseGeometry) -> list[Stroke]:
    rings = []
    for poly in _polygons(geom):
        for ring in (poly.exterior, *poly.interiors):
            rings.append([(x, y) for x, y in ring.coords])
    return rings


def _cover(region: BaseGeometry, line_width: float) -> BaseGeometry:
    """첫 선이 덮는 영역."""
    return region.buffer(-line_width / 2).buffer(line_width / 2)


def _fill(region: BaseGeometry, line_width: float, style: str) -> list[Stroke]:
    strokes = _rings(region.buffer(-line_width / 2))
    if style == "fill" and strokes:
        # 줄인 결과를 다시 줄이면 둥근 모서리 오차가 쌓이므로 매번 원 영역에서 줄임.
        depth = 1.5 * line_width
        while not (inset := region.buffer(-depth)).is_empty:
            strokes += _rings(inset)
            depth += line_width
    return strokes


def _keeps_shape(region: BaseGeometry, grown: BaseGeometry) -> bool:
    """굵힌 영역이 이웃 획과 합쳐지거나 구멍을 막지 않았는지."""
    if len(_polygons(grown)) < len(_polygons(region)):
        return False
    for poly in _polygons(region):
        for ring in poly.interiors:
            hole = Polygon(ring)
            if hole.difference(grown).area < HOLE_KEEP_RATIO * hole.area:
                return False
    return True


def _embolden(judge: _Judge, char_height: float) -> BaseGeometry | None:
    """fill 이 굵기 부족 없이 통과하는 최소 거리만큼 바깥으로 불린 영역."""
    region, line_width = judge.region, judge.line_width
    d_max = min(EMBOLDEN_MAX_EM * char_height, EMBOLDEN_MAX_LINE_WIDTHS * line_width)
    if judge.too_thin(_cover(region.buffer(d_max), line_width)):
        return None
    lo, hi = 0.0, d_max
    for _ in range(EMBOLDEN_SEARCH_STEPS):
        mid = (lo + hi) / 2
        if judge.too_thin(_cover(region.buffer(mid), line_width)):
            lo = mid
        else:
            hi = mid
    grown = region.buffer(hi)
    return grown if _keeps_shape(region, grown) else None


def _length(path: Stroke) -> float:
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def _walk(adj: dict[Point, set[Point]], start: Point, step: Point) -> Stroke:
    """start 에서 step 방향으로 차수 2 꼭짓점을 따라 끝점, 갈림점, 또는 start 까지 감."""
    path = [start, step]
    while len(adj[path[-1]]) == 2:
        a, b = adj[path[-1]]
        nxt = a if b == path[-2] else b
        path.append(nxt)
        if nxt == start:
            break
    return path


def _remove(adj: dict[Point, set[Point]], path: Stroke) -> None:
    for a, b in zip(path, path[1:]):
        adj[a].discard(b)
        adj[b].discard(a)
    for n in path:
        if n in adj and not adj[n]:
            del adj[n]


def _prune(adj: dict[Point, set[Point]], limit: float) -> None:
    changed = True
    while changed:
        changed = False
        branches = [_walk(adj, n, next(iter(s))) for n, s in adj.items() if len(s) == 1]
        for path in sorted(branches, key=_length):
            if _length(path) >= limit:
                break
            intact = all(b in adj.get(a, ()) for a, b in zip(path, path[1:]))
            if intact and len(adj[path[0]]) == 1 and len(adj[path[-1]]) >= 3:
                _remove(adj, path)
                changed = True


def _drop_short_parts(adj: dict[Point, set[Point]], limit: float,
                      keep: Callable[[set[Point]], bool] = lambda part: False) -> None:
    """총 길이가 limit 미만인 연결 조각을 지움. keep 이 참인 조각은 남김."""
    seen: set[Point] = set()
    for start in list(adj):
        if start in seen:
            continue
        part, stack = {start}, [start]
        while stack:
            for m in adj[stack.pop()]:
                if m not in part:
                    part.add(m)
                    stack.append(m)
        seen |= part
        if sum(math.dist(a, b) for a in part for b in adj[a]) / 2 < limit and not keep(part):
            for n in part:
                del adj[n]


def _add_line(adj: dict[Point, set[Point]], coords: object) -> None:
    pts = [(round(x, SKELETON_COORD_DIGITS), round(y, SKELETON_COORD_DIGITS)) for x, y in coords]
    for a, b in zip(pts, pts[1:]):
        if a != b:
            adj[a].add(b)
            adj[b].add(a)


def _chains(adj: dict[Point, set[Point]]) -> list[Stroke]:
    """그래프를 갈림점 사이 사슬로 나눔. 갈림점 없는 고리도 포함."""
    used: set[frozenset[Point]] = set()
    chains = []
    starts = [n for n in adj if len(adj[n]) != 2] + list(adj)
    for n in starts:
        for m in adj[n]:
            if frozenset((n, m)) in used:
                continue
            path = _walk(adj, n, m)
            used.update(frozenset(e) for e in zip(path, path[1:]))
            chains.append(path)
    return chains


def _direction(path: Stroke, reach: float) -> Point:
    """path[0] 에서 reach 만큼 들어간 방향의 단위 벡터."""
    end = path[-1]
    for p in path[1:]:
        if math.dist(path[0], p) >= reach:
            end = p
            break
    dx, dy = end[0] - path[0][0], end[1] - path[0][1]
    norm = math.hypot(dx, dy) or 1.0
    return dx / norm, dy / norm


def _join(chains: list[Stroke], reach: float) -> list[Stroke]:
    """끝점을 공유하는 사슬을 가장 곧게 이어지는 쌍부터 이어 붙여 선 수를 줄임."""
    while True:
        ends: dict[Point, list[tuple[int, bool]]] = defaultdict(list)
        for i, c in enumerate(chains):
            if c[0] != c[-1]:
                ends[c[0]].append((i, False))
                ends[c[-1]].append((i, True))
        best = None
        for group in ends.values():
            for (i, i_tail), (j, j_tail) in itertools.combinations(group, 2):
                if i == j:
                    continue
                u = _direction(chains[i][::-1] if i_tail else chains[i], reach)
                v = _direction(chains[j][::-1] if j_tail else chains[j], reach)
                score = u[0] * v[0] + u[1] * v[1]
                if best is None or score < best[0]:
                    best = (score, i, i_tail, j, j_tail)
        if best is None:
            return chains
        _, i, i_tail, j, j_tail = best
        head = chains[i] if i_tail else chains[i][::-1]
        tail = chains[j][::-1] if j_tail else chains[j]
        chains = [c for k, c in enumerate(chains) if k not in (i, j)] + [head + tail[1:]]


def _skeleton(region: BaseGeometry, spacing: float, line_width: float) -> BaseGeometry:
    """경계 표본의 보로노이 변 중 영역 안쪽 변으로 만든 글자 골격. 모서리 잔가지와 점은 뺌."""
    samples = shapely.get_coordinates(shapely.segmentize(region.boundary, spacing))
    edges = shapely.get_parts(shapely.voronoi_polygons(shapely.multipoints(samples), only_edges=True))
    shapely.prepare(region)
    adj: dict[Point, set[Point]] = defaultdict(set)
    for edge in edges[shapely.contains_properly(region, edges)]:
        _add_line(adj, edge.coords)
    _prune(adj, SKELETON_PRUNE_LINE_WIDTHS * line_width)
    _drop_short_parts(adj, SKELETON_MIN_LENGTH_LINE_WIDTHS * line_width)
    return MultiLineString([(a, b) for a in adj for b in adj[a] if a < b])


class _Judge:
    """원 영역을 덮인 영역이 충분히 덮는지 판정. 중심선용 전체 골격은 처음 필요할 때 한 번 만듦."""

    def __init__(self, region: BaseGeometry, char_height: float, line_width: float) -> None:
        self.region = region
        self.line_width = line_width
        self.piece_limit = min(THIN_LOST_AREA_LINE_WIDTHS_SQ * line_width ** 2, THIN_LOST_PIECE_RATIO * region.area)
        self.total_limit = THIN_LOST_TOTAL_RATIO * region.area
        self.spacing = min(SKELETON_SPACING_LINE_WIDTHS * line_width, SKELETON_SPACING_EM * char_height)
        self._skeleton: BaseGeometry | None = None

    @property
    def skeleton(self) -> BaseGeometry:
        if self._skeleton is None:
            self._skeleton = _skeleton(self.region, self.spacing, self.line_width)
            shapely.prepare(self._skeleton)
        return self._skeleton

    def _lost(self, piece: Polygon, covered: BaseGeometry, reach: BaseGeometry) -> bool:
        lw = self.line_width
        if piece.area > self.piece_limit or piece.distance(covered) > 0:
            return True
        far = piece.difference(reach)
        if far.is_empty:
            return False
        # 조각 안 골격은 가까운 경계만으로 정해지므로 먼 부분 주변만 잘라 계산함.
        # 전체 골격은 선 폭이 작을 때 표본이 많아 느리고, 곡선을 따라 생기는 가는 자투리도 계산에서 빠짐.
        near = MultiPolygon(_polygons(self.region.intersection(far.buffer(SKELETON_CLIP_LINE_WIDTHS * lw))))
        return _skeleton(near, self.spacing, lw).intersection(piece).length > THIN_LOST_SKELETON_LINE_WIDTHS * lw

    def _broken(self, covered: BaseGeometry) -> bool:
        """원 조각마다 덮인 부분이 둘 이상으로 나뉘거나 원래 구멍을 못 둘러싸면 참.
        A 가로획처럼 짧은 연결 획이 빠지면 면적, 골격 기준은 통과해도 여기서 걸림."""
        gap = THIN_JOIN_GAP_LINE_WIDTHS * self.line_width
        for poly in _polygons(self.region):
            parts = _polygons(covered.intersection(poly).buffer(gap / 2))
            if len(parts) != 1:
                return True
            shell = Polygon(parts[0].exterior)
            if not all(shell.contains(Polygon(h).representative_point()) for h in poly.interiors):
                return True
        return False

    def too_thin(self, covered: BaseGeometry) -> bool:
        if covered.is_empty:
            return True
        lost = _polygons(self.region.difference(covered))
        if sum(p.area for p in lost) > self.total_limit:
            return True
        reach = covered.buffer(THIN_CORNER_REACH_LINE_WIDTHS * self.line_width)
        if any(self._lost(p, covered, reach) for p in lost):
            return True
        return self._broken(covered)


def _touches(part: set[Point], adj: dict[Point, set[Point]], covered: BaseGeometry) -> int:
    """조각의 끝점 중 덮인 부분 경계에 닿은 수."""
    ends = [n for n in part if len(adj[n]) == 1]
    return sum(covered.distance(shapely.Point(n)) <= SKELETON_TOUCH_MM for n in ends)


def _center(judge: _Judge, covered: BaseGeometry) -> list[Stroke] | None:
    """덮이지 않은 골격 부분을 중심선으로 그림. 더한 뒤 굵기 부족이 없을 때만 선 목록, 아니면 None."""
    adj: dict[Point, set[Point]] = defaultdict(set)
    for part in shapely.get_parts(shapely.line_merge(judge.skeleton.difference(covered))):
        _add_line(adj, part.coords)
    lw = judge.line_width
    # 모서리로 삐져나온 짧은 조각은 버리되 덮인 부분 둘을 잇는 조각은 남겨 끊김을 막음.
    _drop_short_parts(adj, SKELETON_MIN_LENGTH_LINE_WIDTHS * lw, lambda part: _touches(part, adj, covered) >= 2)
    chains = _join(_chains(adj), SKELETON_PRUNE_LINE_WIDTHS * lw)
    lines = [[(x, y) for x, y in LineString(c).simplify(judge.spacing / 2).coords] for c in chains]
    if not lines:
        return None
    return None if judge.too_thin(covered.union(MultiLineString(lines).buffer(lw / 2))) else lines


def _thinnest_ratio(region_em: BaseGeometry) -> float | None:
    """em 단위 영역이 굵기 부족 없이 채워지는 최대 선 폭/글자 높이 비율."""
    def fits(ratio: float) -> bool:
        return not _Judge(region_em, 1.0, ratio).too_thin(_cover(region_em, ratio))

    # 잃는 면적 기준이 선 폭²에 비례해 판정이 단조롭지 않으므로 이분 탐색 전에 작은 쪽부터 훑음.
    ratio = MIN_HEIGHT_RATIO_START
    if not fits(ratio):
        return None
    while True:
        nxt = ratio * MIN_HEIGHT_RATIO_STEP
        if nxt > MIN_HEIGHT_RATIO_MAX:
            return ratio
        if not fits(nxt):
            break
        ratio = nxt
    lo, hi = ratio, nxt
    for _ in range(MIN_HEIGHT_SEARCH_STEPS):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)
    return lo


def trace_region(region: BaseGeometry, ref_height: float, line_width: float, style: str = "fill",
                 fallback: str = "auto") -> GlyphResult:
    """영역(mm)을 크림 경로로 바꿈. ref_height는 영역 크기 기준 길이로, 골격 표본 간격과 굵히기 한도에 씀.

    advance, min_char_height는 채우지 않음. 호출자가 필요하면 채움.
    """
    strokes = _fill(region, line_width, style)
    judge = _Judge(region, ref_height, line_width)
    covered = _cover(region, line_width)
    if not judge.too_thin(covered):
        return GlyphResult(strokes, 0.0, False, style)
    for stage in FALLBACKS[fallback]:
        if stage == "embolden" and (grown := _embolden(judge, ref_height)) is not None:
            return GlyphResult(_fill(grown, line_width, style), 0.0, False, stage)
        if stage == "center" and (lines := _center(judge, covered)) is not None:
            return GlyphResult(strokes + lines, 0.0, False, stage)
    # 폴백이 모두 실패하면 첫 선 결과를 그대로 두고 굵기 부족으로 표시.
    return GlyphResult(strokes, 0.0, True, style)


class OutlineFont:
    """폰트 파일 하나. 가변 폰트는 weight 를 주면 그 wght 인스턴스, 아니면 기본 인스턴스를 씀."""

    def __init__(self, path: Path, weight: float | None = None) -> None:
        try:
            data = Path(path).read_bytes()
            if data[:4] == COLLECTION_TAG:
                raise FontGlyphError(f"폰트 컬렉션(ttc)은 지원하지 않음: {path}")
            # 파일 대신 메모리에서 읽어 열린 파일을 남기지 않음.
            self._font = TTFont(io.BytesIO(data), lazy=weight is None)
            if weight is not None:
                self._font = self._instance(self._font, weight, path)
            self._cmap = self._font.getBestCmap() or {}
            self._glyph_set = self._font.getGlyphSet()
            self._units_per_em = self._font["head"].unitsPerEm
            self._hmtx = self._font["hmtx"]
        except (OSError, TTLibError, KeyError, AssertionError, struct.error) as exc:
            raise FontGlyphError(f"폰트 읽기 실패: {path}: {exc}") from exc
        lo, hi = UNITS_PER_EM_RANGE
        if not lo <= self._units_per_em <= hi:
            raise FontGlyphError(f"unitsPerEm {self._units_per_em} 는 허용 범위 {lo}–{hi} 밖임: {path}")
        self.weight = weight
        self._cache: dict[tuple[str, float, float, str, str], GlyphResult] = {}
        self._ratio_cache: dict[str, float | None] = {}

    @staticmethod
    def _instance(font: TTFont, weight: float, path: Path) -> TTFont:
        axes = {a.axisTag: a for a in font["fvar"].axes} if "fvar" in font else {}
        if WEIGHT_AXIS not in axes:
            raise FontGlyphError(f"wght 축이 없는 폰트라 굵기 지정 불가: {path}")
        axis = axes[WEIGHT_AXIS]
        if not axis.minValue <= weight <= axis.maxValue:
            raise FontGlyphError(f"굵기 {weight:g} 는 허용 범위 {axis.minValue:g}–{axis.maxValue:g} 밖임: {path}")
        try:
            inst = instancer.instantiateVariableFont(font, {WEIGHT_AXIS: weight})
            buf = io.BytesIO()
            inst.save(buf)
        except (ValueError, TypeError, TTLibError) as exc:
            raise FontGlyphError(f"굵기 {weight:g} 인스턴스 생성 실패: {path}: {exc}") from exc
        buf.seek(0)
        return TTFont(buf, lazy=True)

    def has_glyph(self, ch: str) -> bool:
        return ord(ch) in self._cmap if len(ch) == 1 else False

    def glyph_paths(self, ch: str, char_height: float, line_width: float, style: str = "fill",
                    fallback: str = "auto") -> GlyphResult:
        key = (ch, char_height, line_width, style, fallback)
        if key not in self._cache:
            try:
                self._cache[key] = self._build(ch, char_height, line_width, style, fallback)
            except (shapely.errors.ShapelyError, struct.error) as exc:
                raise FontGlyphError(f"글리프 처리 실패: {ch!r}: {exc}") from exc
        cached = self._cache[key]
        return replace(cached, strokes=[list(s) for s in cached.strokes])

    def _draw(self, ch: str, name: str, scale: float, tolerance: float) -> BaseGeometry:
        pen = _FlattenPen(self._glyph_set, scale, tolerance)
        try:
            self._glyph_set[name].draw(pen)
        except (TTLibError, KeyError, ValueError, IndexError, struct.error) as exc:
            raise FontGlyphError(f"글리프 읽기 실패: {ch!r}: {exc}") from exc
        return _region(pen.contours)

    def _min_char_height(self, ch: str, name: str, line_width: float) -> float | None:
        if name not in self._ratio_cache:
            region_em = self._draw(ch, name, 1 / self._units_per_em, MIN_HEIGHT_FLATTEN_EM)
            self._ratio_cache[name] = _thinnest_ratio(region_em)
        ratio = self._ratio_cache[name]
        return None if ratio is None else MIN_HEIGHT_MARGIN * line_width / ratio

    def _build(self, ch: str, char_height: float, line_width: float, style: str, fallback: str) -> GlyphResult:
        if len(ch) != 1:
            raise FontGlyphError(f"글자 하나만 받음: {ch!r}")
        if style not in STYLES:
            raise FontGlyphError(f"스타일은 {', '.join(STYLES)} 중 하나: {style}")
        if fallback not in FALLBACKS:
            raise FontGlyphError(f"폴백은 {', '.join(FALLBACKS)} 중 하나: {fallback}")
        if char_height <= 0 or line_width <= 0:
            raise FontGlyphError("char_height, line_width는 0보다 커야 함")
        scale = char_height / self._units_per_em
        name = self._cmap.get(ord(ch))
        if name is None:
            if ch.isspace():
                return GlyphResult([], DEFAULT_SPACE_EM * char_height, False, "none")
            raise FontGlyphError(f"폰트에 없는 글자: {ch!r}")
        try:
            advance = self._hmtx[name][0] * scale
        except (TTLibError, KeyError, IndexError, struct.error) as exc:
            raise FontGlyphError(f"글자 폭 읽기 실패: {ch!r}: {exc}") from exc
        region = self._draw(ch, name, scale, FLATTEN_TOLERANCE_MM)
        if region.is_empty:
            return GlyphResult([], advance, False, "none")
        traced = trace_region(region, char_height, line_width, style, fallback)
        return replace(traced, advance=advance, min_char_height=self._min_char_height(ch, name, line_width))
