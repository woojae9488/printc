#!/usr/bin/env python3
"""폰트 이름이나 파일 경로를 로컬 폰트 파일 경로로 바꿈.

이름이면 캐시를 먼저 보고, 없으면 Google Fonts 저장소(google/fonts)에서 내려받음.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import BinaryIO

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_ENV = "PRINTC_FONT_CACHE"
DEFAULT_CACHE = Path("~/.cache/printc/fonts")
LICENSE_DIRS = ("ofl", "apache", "ufl")
LISTING_URL = "https://api.github.com/repos/google/fonts/contents/{license}/{slug}"
ALLOWED_HOSTS = frozenset({"api.github.com", "raw.githubusercontent.com"})
DOWNLOAD_PREFIX = "/google/fonts/"
TIMEOUT_S = 30
MAX_FONT_BYTES = 30 * 1024 * 1024
MAX_LISTING_BYTES = 1024 * 1024
CHUNK_BYTES = 64 * 1024
FONT_SUFFIXES = (".ttf", ".otf")
FONT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true")
TTC_MAGIC = b"ttcf"
DIR_MODE = 0o700
FILE_MODE = 0o600
PATH_LIKE = re.compile(r"[/\\]|^[~.]|\.[A-Za-z0-9]{2,5}$")
USER_AGENT = "printc-fontsrc"
HTTP_FORBIDDEN = 403
RATE_LIMIT_HINT = "API 한도 초과 가능. 잠시 후 재시도 또는 폰트 파일 경로 지정"


class FontError(Exception):
    """사용자에게 보일 폰트 오류."""


def _say(message: str) -> None:
    print(message, file=sys.stderr)


# ---------- 검증 ----------

def check_magic(head: bytes, what: str) -> None:
    if head[:4] == TTC_MAGIC:
        raise FontError(f"TTC(폰트 모음) 미지원: {what}")
    if head[:4] not in FONT_MAGICS:
        raise FontError(f"폰트 파일 아님(헤더 불량): {what}")


def check_font_file(path: Path) -> Path:
    if path.suffix.lower() not in FONT_SUFFIXES:
        raise FontError(f"폰트 확장자는 .ttf, .otf: {path}")
    try:
        with path.open("rb") as f:
            head = f.read(4)
    except OSError as exc:
        raise FontError(f"폰트 읽기 실패: {path}: {exc}") from exc
    check_magic(head, str(path))
    return path


def slugify(family: str) -> str:
    """google/fonts 디렉터리 이름 규칙. 영문 소문자, 숫자만 남김."""
    slug = re.sub(r"[^a-z0-9]", "", family.lower())
    if not slug:
        raise FontError(f"폰트 이름은 영문 패밀리 이름: {family!r}")
    return slug


def _check_name(name: str) -> str:
    """저장소 목록의 파일 이름이 캐시 디렉터리를 벗어나지 않는지 확인함."""
    if not name or "/" in name or "\\" in name or ".." in name or name.startswith(".") or "\0" in name:
        raise FontError(f"허용되지 않는 파일 이름: {name!r}")
    return name


def pick_font(names: list[str]) -> str:
    """우선순위: 이름에 -Regular 포함 정적 ttf > 가변 [wght] ttf > 첫 ttf/otf. 같은 순위 여럿이면 오류."""
    fonts = [n for n in names if n.lower().endswith(FONT_SUFFIXES)]
    if not fonts:
        raise FontError("ttf, otf 파일 없음")
    ttf = [n for n in fonts if n.lower().endswith(".ttf")]
    tiers = (
        [n for n in ttf if "-Regular" in n and "[" not in n],
        [n for n in ttf if re.search(r"\[[^]]*wght", n) and "Italic" not in n],
    )
    for tier in tiers:
        exact = [n for n in tier if n.endswith("-Regular.ttf")]
        tier = exact or tier
        if len(tier) == 1:
            return tier[0]
        if tier:
            raise FontError(f"후보 여러 개: {', '.join(tier)}. 파일 경로로 지정")
    return fonts[0]


# ---------- 캐시 ----------

def cache_root(cache_dir: Path | None = None) -> Path:
    root = cache_dir if cache_dir is not None else Path(os.environ.get(CACHE_ENV) or DEFAULT_CACHE)
    root = root.expanduser().resolve()
    if root.is_relative_to(REPO_ROOT):
        raise FontError(f"폰트 캐시가 저장소 안: {root}. {CACHE_ENV}로 저장소 밖 지정")
    return root


def _cached(family_dir: Path) -> Path | None:
    if not family_dir.is_dir():
        return None
    names = sorted(p.name for p in family_dir.iterdir() if p.is_file() and not p.name.startswith("."))
    if not any(n.lower().endswith(FONT_SUFFIXES) for n in names):
        return None
    return check_font_file(family_dir / pick_font(names))


# ---------- 네트워크 ----------

def _check_url(url: str, path_prefix: str | None = None) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise FontError(f"허용되지 않는 주소: {url}")
    if path_prefix is not None:
        path = urllib.parse.unquote(parts.path)
        if not path.startswith(path_prefix) or {".", ".."} & set(path.split("/")):
            raise FontError(f"허용되지 않는 주소: {url}")


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가기 전에 새 주소를 확인함."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        try:
            _check_url(newurl)
        except FontError:
            fp.close()
            raise
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_RedirectGuard)


def _open(request: urllib.request.Request, timeout: float):  # type: ignore[no-untyped-def]
    return _OPENER.open(request, timeout=timeout)


def _stream(url: str, limit: int, sink: BinaryIO) -> None:
    """url 본문을 limit 바이트까지 sink에 씀. 리다이렉트는 단계마다, 최종 주소는 한 번 더 확인함."""
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _open(request, TIMEOUT_S) as resp:
        _check_url(resp.geturl())
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > limit:
            raise FontError(f"크기 상한 초과: {url} ({int(length)} > {limit} 바이트)")
        total = 0
        while chunk := resp.read(CHUNK_BYTES):
            total += len(chunk)
            if total > limit:
                raise FontError(f"크기 상한 초과: {url} (> {limit} 바이트)")
            sink.write(chunk)


def _network_error(url: str, exc: Exception) -> FontError:
    if isinstance(exc, urllib.error.HTTPError):
        hint = f". {RATE_LIMIT_HINT}" if exc.code == HTTP_FORBIDDEN else ""
        return FontError(f"조회 실패: HTTP {exc.code}: {url}{hint}")
    reason = getattr(exc, "reason", exc)
    return FontError(f"네트워크 오류(오프라인 여부 확인): {url}: {reason}")


def _listing(license_dir: str, slug: str) -> list[dict] | None:
    """디렉터리 목록. 없으면 None."""
    url = LISTING_URL.format(license=license_dir, slug=slug)
    buf = io.BytesIO()
    try:
        _stream(url, MAX_LISTING_BYTES, buf)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise _network_error(url, exc) from exc
    except OSError as exc:
        raise _network_error(url, exc) from exc
    buf.seek(0)
    try:
        items = json.loads(buf.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FontError(f"목록 형식 오류: {url}") from exc
    if not isinstance(items, list):
        raise FontError(f"목록 형식 오류: {url}")
    return [i for i in items if isinstance(i, dict) and i.get("type") == "file" and isinstance(i.get("name"), str)]


def _download(url: str, dest: Path) -> None:
    """임시 파일에 받아 헤더를 확인한 뒤 원자적으로 옮김."""
    fd, tmp_name = tempfile.mkstemp(prefix=".", suffix=".part", dir=dest.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            try:
                _stream(url, MAX_FONT_BYTES, f)
            except OSError as exc:
                raise _network_error(url, exc) from exc
            f.flush()
            os.fsync(f.fileno())
        with tmp.open("rb") as f:
            check_magic(f.read(4), url)
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def fetch_family(family: str, slug: str, family_dir: Path) -> Path:
    _say(f"폰트 내려받는 중: {family}")
    for license_dir in LICENSE_DIRS:
        items = _listing(license_dir, slug)
        if items is None:
            continue
        by_name = {i["name"]: i for i in items}
        name = _check_name(pick_font(sorted(by_name)))
        item = by_name[name]
        url = item.get("download_url")
        if not isinstance(url, str):
            raise FontError(f"download_url 없음: {name}")
        _check_url(url, DOWNLOAD_PREFIX)
        size = item.get("size")
        if isinstance(size, int) and size > MAX_FONT_BYTES:
            raise FontError(f"크기 상한 초과: {name} ({size} > {MAX_FONT_BYTES} 바이트)")
        if not family_dir.is_dir():
            family_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
            os.chmod(family_dir, DIR_MODE)  # umask 무관하게 맞춤
        dest = family_dir / name
        _download(url, dest)
        _say(f"폰트 받음: {dest} (google/fonts {license_dir}/{slug}, 라이선스 {license_dir.upper()} 조건 따름)")
        return dest
    raise FontError(f"폰트 없음: {family} (google/fonts {', '.join(LICENSE_DIRS)}/{slug})")


# ---------- 진입점 ----------

def resolve_font(spec: str, cache_dir: Path | None = None) -> Path:
    """폰트 파일 경로 또는 패밀리 이름을 로컬 폰트 파일 경로로 바꿈."""
    spec = spec.strip()
    if not spec:
        raise FontError("폰트 지정이 비어 있음")
    path = Path(spec).expanduser()
    if path.exists():
        if not path.is_file():
            raise FontError(f"폰트 경로가 파일 아님: {path}")
        return check_font_file(path.resolve())
    if PATH_LIKE.search(spec):
        raise FontError(f"폰트 파일 없음: {path}")
    slug = slugify(spec)
    family_dir = cache_root(cache_dir) / slug
    return _cached(family_dir) or fetch_family(spec, slug, family_dir)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        _say("사용법: fontsrc.py <폰트 이름 또는 파일 경로>")
        return 2
    try:
        print(resolve_font(args[0]))
    except FontError as exc:
        _say(f"오류: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
