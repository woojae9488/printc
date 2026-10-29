"""fontsrc 단위 테스트. 네트워크는 fontsrc._open 또는 전송 handler 모킹으로 대체함."""

from __future__ import annotations

import email.message
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from contextlib import redirect_stderr
from pathlib import Path
from typing import Callable
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fontsrc as fs  # noqa: E402

TTF = b"\x00\x01\x00\x00" + b"\x00" * 60
API = "https://api.github.com/repos/google/fonts/contents"
RAW = "https://raw.githubusercontent.com/google/fonts/main"


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str, length: int | None = None) -> None:
        super().__init__(body)
        self.url = url
        self.headers = {} if length is None else {"Content-Length": str(length)}

    def geturl(self) -> str:
        return self.url


def listing(license_dir: str, slug: str, *names: str) -> bytes:
    return json.dumps([
        {"name": n, "type": "file", "size": len(TTF), "download_url": f"{RAW}/{license_dir}/{slug}/{n}"}
        for n in names
    ]).encode()


Net = Callable[..., object]


class FakeNet:
    """url별 응답(bytes 또는 FakeResponse). 없는 url은 404."""

    def __init__(self, routes: dict[str, bytes | FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, request: object, timeout: float) -> FakeResponse:
        url = request.full_url  # type: ignore[attr-defined]
        self.calls.append(url)
        if url not in self.routes:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        body = self.routes[url]
        return body if isinstance(body, FakeResponse) else FakeResponse(body, url)


class FakeTransport:
    """실제 opener 아래의 전송 계층. url별 본문(bytes) 또는 리다이렉트 대상(str). 없는 url은 404."""

    def __init__(self, routes: dict[str, bytes | str]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def respond(self, req: urllib.request.Request) -> urllib.response.addinfourl:
        url = req.full_url
        self.calls.append(url)
        headers = email.message.Message()
        body = self.routes.get(url)
        if isinstance(body, str):
            headers["Location"] = body
            code, msg, body = 302, "Found", b""
        elif body is None:
            code, msg, body = 404, "Not Found", b""
        else:
            code, msg = 200, "OK"
        resp = urllib.response.addinfourl(io.BytesIO(body), headers, url, code)
        resp.msg = msg  # type: ignore[attr-defined]
        return resp

    def opener(self) -> urllib.request.OpenerDirector:
        transport = self

        class Https(urllib.request.HTTPSHandler):
            def https_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return transport.respond(req)

        class Http(urllib.request.HTTPHandler):
            def http_open(self, req: urllib.request.Request) -> urllib.response.addinfourl:
                return transport.respond(req)

        # noinspection PyProtectedMember
        return urllib.request.build_opener(fs._RedirectGuard, Https, Http)


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name).resolve() / "fonts"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def resolve(self, spec: str, net: Net) -> tuple[Path, str]:
        err = io.StringIO()
        with mock.patch.object(fs, "_open", net), redirect_stderr(err):
            path = fs.resolve_font(spec, self.cache)
        return path, err.getvalue()

    def assert_fails(self, spec: str, net: Net, pattern: str) -> None:
        with mock.patch.object(fs, "_open", net), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(fs.FontError, pattern):
                fs.resolve_font(spec, self.cache)


class PathInputTest(CacheTest):
    def test_existing_file(self) -> None:
        font = Path(self._tmp.name) / "my.ttf"
        font.write_bytes(TTF)
        path, _ = self.resolve(str(font), FakeNet({}))
        self.assertEqual(path, font.resolve())

    def test_bad_files(self) -> None:
        bad = Path(self._tmp.name) / "bad.ttf"
        bad.write_bytes(b"<html>")
        self.assert_fails(str(bad), FakeNet({}), "헤더 불량")
        ttc = Path(self._tmp.name) / "c.otf"
        ttc.write_bytes(b"ttcf" + TTF)
        self.assert_fails(str(ttc), FakeNet({}), "TTC")
        txt = Path(self._tmp.name) / "a.txt"
        txt.write_bytes(TTF)
        self.assert_fails(str(txt), FakeNet({}), "확장자")

    def test_missing_path_like(self) -> None:
        net = FakeNet({})
        for spec in ("fonts/none.ttf", "none.otf", "../../etc/passwd", "~/x"):
            self.assert_fails(spec, net, "폰트 파일 없음")
        self.assertEqual(net.calls, [])


class ResolveFamilyTest(CacheTest):
    def test_download_then_cache_hit(self) -> None:
        net = FakeNet({
            f"{API}/ofl/nanumgothic": listing("ofl", "nanumgothic", "NanumGothic-Bold.ttf",
                                              "NanumGothic-Regular.ttf", "OFL.txt"),
            f"{RAW}/ofl/nanumgothic/NanumGothic-Regular.ttf": TTF,
        })
        path, err = self.resolve("Nanum Gothic", net)
        self.assertEqual(path, self.cache / "nanumgothic" / "NanumGothic-Regular.ttf")
        self.assertEqual(path.read_bytes(), TTF)
        self.assertIn("폰트 내려받는 중: Nanum Gothic", err)
        self.assertIn("ofl", err)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), fs.FILE_MODE)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), fs.DIR_MODE)
        self.assertEqual([p.name for p in path.parent.iterdir()], ["NanumGothic-Regular.ttf"])

        cached = FakeNet({})
        self.assertEqual(self.resolve("nanum gothic", cached)[0], path)
        self.assertEqual(cached.calls, [])

    def test_license_order(self) -> None:
        net = FakeNet({
            f"{API}/ufl/ubuntu": listing("ufl", "ubuntu", "Ubuntu-Regular.ttf"),
            f"{RAW}/ufl/ubuntu/Ubuntu-Regular.ttf": TTF,
        })
        _, err = self.resolve("Ubuntu", net)
        self.assertEqual(net.calls[:3], [f"{API}/ofl/ubuntu", f"{API}/apache/ubuntu", f"{API}/ufl/ubuntu"])
        self.assertIn("ufl", err)

    def test_env_cache(self) -> None:
        with mock.patch.dict(os.environ, {fs.CACHE_ENV: str(self.cache)}):
            self.assertEqual(fs.cache_root(), self.cache)
        with self.assertRaisesRegex(fs.FontError, "저장소 안"):
            fs.cache_root(fs.REPO_ROOT / "fonts")

    def test_not_found(self) -> None:
        self.assert_fails("No Such Font", FakeNet({}), "폰트 없음: No Such Font")

    def test_offline(self) -> None:
        def offline(*_: object, **__: object) -> None:
            raise urllib.error.URLError("no network")

        self.assert_fails("Nanum Gothic", offline, "네트워크 오류")

    def test_rate_limited(self) -> None:
        def failing(code: int):  # type: ignore[no-untyped-def]
            def fail(request: object, *_: object) -> None:
                raise urllib.error.HTTPError(request.full_url, code, "Error", {}, None)  # type: ignore

            return fail

        self.assert_fails("Nanum Gothic", failing(403), f"HTTP 403: .+{fs.RATE_LIMIT_HINT}")
        with mock.patch.object(fs, "_open", failing(500)), redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(fs.FontError, "HTTP 500") as ctx:
                fs.resolve_font("Nanum Gothic", self.cache)
        self.assertNotIn(fs.RATE_LIMIT_HINT, str(ctx.exception))

    def test_non_ascii_name(self) -> None:
        self.assert_fails("나눔고딕", FakeNet({}), "영문 패밀리")


class DownloadSafetyTest(CacheTest):
    @staticmethod
    def family(name: str, body: bytes | FakeResponse, url: str | None = None) -> FakeNet:
        url = url or f"{RAW}/ofl/foo/{name}"
        items = [{"name": name, "type": "file", "size": 10, "download_url": url}]
        return FakeNet({f"{API}/ofl/foo": json.dumps(items).encode(), url: body})

    def assert_no_files(self) -> None:
        files = [p for p in self.cache.rglob("*") if p.is_file()] if self.cache.exists() else []
        self.assertEqual(files, [])

    def test_bad_header(self) -> None:
        self.assert_fails("Foo", self.family("Foo-Regular.ttf", b"<html>not a font</html>"), "헤더 불량")
        self.assert_fails("Foo", self.family("Foo-Regular.ttf", b"ttcf" + TTF), "TTC")
        self.assert_no_files()

    def test_too_large(self) -> None:
        url = f"{RAW}/ofl/foo/Foo-Regular.ttf"
        with mock.patch.object(fs, "MAX_FONT_BYTES", 100):
            self.assert_fails("Foo", self.family("Foo-Regular.ttf", TTF * 2), "크기 상한")
            declared = FakeResponse(TTF, url, length=10 ** 9)
            self.assert_fails("Foo", self.family("Foo-Regular.ttf", declared), "크기 상한")
        self.assert_no_files()

    def test_disallowed_host(self) -> None:
        for url in ("https://evil.example/Foo-Regular.ttf", "http://raw.githubusercontent.com/Foo-Regular.ttf",
                    "file:///etc/passwd"):
            self.assert_fails("Foo", self.family("Foo-Regular.ttf", TTF, url), "허용되지 않는 주소")
        self.assert_no_files()

    def test_redirect_to_other_host(self) -> None:
        url = f"{RAW}/ofl/foo/Foo-Regular.ttf"
        redirected = FakeResponse(TTF, "https://evil.example/Foo-Regular.ttf")
        self.assert_fails("Foo", self.family("Foo-Regular.ttf", redirected, url), "허용되지 않는 주소")
        self.assert_no_files()

    def test_name_escape(self) -> None:
        for name in ("../../evil-Regular.ttf", "..\\evil-Regular.ttf", ".hidden-Regular.ttf"):
            self.assert_fails("Foo", self.family(name, TTF, f"{RAW}/x.ttf"), "허용되지 않는 파일 이름")
        self.assertFalse((self.cache.parent / "evil-Regular.ttf").exists())

    def test_slug_has_no_separators(self) -> None:
        self.assertEqual(fs.slugify("Noto Sans KR"), "notosanskr")
        self.assertEqual(fs.slugify("..evil..name"), "evilname")
        with self.assertRaises(fs.FontError):
            fs.slugify("..")


class RedirectTest(CacheTest):
    """실제 opener와 _RedirectGuard로 리다이렉트 단계별 검증."""

    LISTING = f"{API}/ofl/foo"
    FONT = f"{RAW}/ofl/foo/Foo-Regular.ttf"

    def run_family(self, routes: dict[str, bytes | str], download_url: str | None = None) -> FakeTransport:
        items = [{"name": "Foo-Regular.ttf", "type": "file", "size": 10, "download_url": download_url or self.FONT}]
        routes.setdefault(self.LISTING, json.dumps(items).encode())
        transport = FakeTransport(routes)
        self.patcher = mock.patch.object(fs, "_OPENER", transport.opener())
        return transport

    def resolve_real(self) -> Path:
        with self.patcher, redirect_stderr(io.StringIO()):
            return fs.resolve_font("Foo", self.cache)

    def assert_rejected(self, pattern: str = "허용되지 않는 주소") -> None:
        with self.patcher, redirect_stderr(io.StringIO()), self.assertRaisesRegex(fs.FontError, pattern):
            fs.resolve_font("Foo", self.cache)

    def test_redirect_to_other_host_not_followed(self) -> None:
        net = self.run_family({self.FONT: "https://evil.example/Foo-Regular.ttf",
                               "https://evil.example/Foo-Regular.ttf": TTF})
        self.assert_rejected()
        self.assertEqual(net.calls, [self.LISTING, self.FONT])
        self.assertFalse(self.cache.exists() and any(p.is_file() for p in self.cache.rglob("*")))

    def test_redirect_listing_to_other_host_not_followed(self) -> None:
        net = self.run_family({self.LISTING: "https://evil.example/list"})
        self.assert_rejected()
        self.assertEqual(net.calls, [self.LISTING])

    def test_redirect_to_http_not_followed(self) -> None:
        http = "http://raw.githubusercontent.com/google/fonts/main/ofl/foo/Foo-Regular.ttf"
        net = self.run_family({self.FONT: http, http: TTF})
        self.assert_rejected()
        self.assertEqual(net.calls, [self.LISTING, self.FONT])

    def test_redirect_between_allowed_hosts(self) -> None:
        moved_list = "https://api.github.com/repositories/1/contents/ofl/foo"
        moved_font = f"{RAW}/ofl/foo/moved/Foo-Regular.ttf"
        items = [{"name": "Foo-Regular.ttf", "type": "file", "size": 10, "download_url": self.FONT}]
        net = self.run_family({self.LISTING: moved_list, moved_list: json.dumps(items).encode(),
                               self.FONT: moved_font, moved_font: TTF})
        path = self.resolve_real()
        self.assertEqual(path.read_bytes(), TTF)
        self.assertEqual(net.calls, [self.LISTING, moved_list, self.FONT, moved_font])

    def test_download_path_prefix(self) -> None:
        for url in ("https://raw.githubusercontent.com/evil/fonts/main/Foo-Regular.ttf",
                    f"{RAW}/../../evil/fonts/Foo-Regular.ttf", f"{RAW}/%2e%2e/%2e%2e/evil/Foo-Regular.ttf",
                    "https://api.github.com/repos/evil/Foo-Regular.ttf"):
            net = self.run_family({url: TTF}, download_url=url)
            self.assert_rejected()
            self.assertEqual(net.calls, [self.LISTING])

    def test_existing_dir_mode_kept(self) -> None:
        family_dir = self.cache / "foo"
        family_dir.mkdir(parents=True)
        family_dir.chmod(0o750)
        self.run_family({self.FONT: TTF})
        self.resolve_real()
        self.assertEqual(stat.S_IMODE(family_dir.stat().st_mode), 0o750)


class PickFontTest(unittest.TestCase):
    def test_priority(self) -> None:
        self.assertEqual(fs.pick_font(["A-Bold.ttf", "A[wght].ttf", "A-Regular.ttf"]), "A-Regular.ttf")
        self.assertEqual(fs.pick_font(["A-Italic[wght].ttf", "A[wght].ttf", "OFL.txt"]), "A[wght].ttf")
        self.assertEqual(fs.pick_font(["A[wdth,wght].ttf", "A-Italic[wdth,wght].ttf"]), "A[wdth,wght].ttf")
        self.assertEqual(fs.pick_font(["A-Bold.otf", "A-Light.otf"]), "A-Bold.otf")
        self.assertEqual(fs.pick_font(["A-Regular.ttf", "A-RegularItalic.ttf"]), "A-Regular.ttf")

    def test_ambiguous(self) -> None:
        with self.assertRaisesRegex(fs.FontError, "후보 여러 개"):
            fs.pick_font(["A-Regular.ttf", "ASC-Regular.ttf"])
        with self.assertRaisesRegex(fs.FontError, "후보 여러 개"):
            fs.pick_font(["A[wght].ttf", "AMono[wght].ttf"])

    def test_no_font(self) -> None:
        with self.assertRaisesRegex(fs.FontError, "ttf, otf 파일 없음"):
            fs.pick_font(["OFL.txt", "METADATA.pb"])


if __name__ == "__main__":
    unittest.main()
