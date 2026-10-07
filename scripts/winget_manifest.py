"""Render the WinGet manifests for a release (#185).

    python3 scripts/winget_manifest.py --version X.Y.Z --zip dist/promptmend-X.Y.Z-windows-x64.zip

The templates in packaging/winget/ (version, installer and en-US defaultLocale manifests of
the package `vlastimilbures.PromptMend`) get the version, the Release asset's URL, its
SHA-256 (from the local zip, which `gh attestation verify` can check first, or `--sha256`),
the release date and the Release's notes URL. The result goes where winget-pkgs keeps it,
`<out>/manifests/v/vlastimilbures/PromptMend/X.Y.Z/`, ready for `winget validate --manifest`
and, with the owner's go, a pull request to microsoft/winget-pkgs (never made by this script).

`--url` replaces the installer URL; CI uses it to install the zip from a local server.

Standard library only, and offline: it reads files and never downloads anything.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "packaging" / "winget"
PACKAGE_ID = "vlastimilbures.PromptMend"
REPO_URL = "https://github.com/vlastimilbures/promptmend"
FILES = (
    f"{PACKAGE_ID}.yaml",
    f"{PACKAGE_ID}.installer.yaml",
    f"{PACKAGE_ID}.locale.en-US.yaml",
)
PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")
_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_URL = re.compile(r"https?://[^\s'\"#]+\.zip")


class ManifestError(Exception):
    """An input the manifests cannot take; the message says which."""


def zip_name(version: str) -> str:
    return f"promptmend-{version}-windows-x64.zip"


def release_url(version: str) -> str:
    return f"{REPO_URL}/releases/download/v{version}/{zip_name(version)}"


def notes_url(version: str) -> str:
    return f"{REPO_URL}/releases/tag/v{version}"


def manifest_dir(out: Path, version: str) -> Path:
    """winget-pkgs' layout: manifests/<first letter of the id, lower case>/<id as folders>/<v>."""
    publisher, name = PACKAGE_ID.split(".", 1)
    return out / "manifests" / publisher[0].lower() / publisher / name / version


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check(version: str, sha256: str, url: str, release_date: str) -> None:
    if not _VERSION.fullmatch(version):
        raise ManifestError(f"not a release version X.Y.Z: {version!r}")
    if not _SHA256.fullmatch(sha256):
        raise ManifestError(f"not a SHA-256 (64 hex digits): {sha256!r}")
    if not _URL.fullmatch(url):
        raise ManifestError(f"not an http(s) URL of a .zip: {url!r}")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", release_date):
            raise ValueError
        datetime.date.fromisoformat(release_date)
    except ValueError:
        raise ManifestError(f"not a date YYYY-MM-DD: {release_date!r}") from None


def render(
    version: str,
    sha256: str,
    url: str | None = None,
    release_date: str | None = None,
    templates: Path = TEMPLATES,
) -> dict[str, str]:
    """Each manifest's file name and text. WinGet-pkgs writes the hash in upper case."""
    url = url or release_url(version)
    release_date = release_date or datetime.datetime.now(datetime.UTC).date().isoformat()
    check(version, sha256, url, release_date)
    values: Mapping[str, str] = {
        "__VERSION__": version,
        "__INSTALLER_URL__": url,
        "__INSTALLER_SHA256__": sha256.upper(),
        "__RELEASE_DATE__": release_date,
        "__RELEASE_NOTES_URL__": notes_url(version),
    }
    rendered: dict[str, str] = {}
    for name in FILES:
        text = (templates / name).read_text("utf-8")
        for key, value in values.items():
            text = text.replace(key, value)
        if left := sorted(set(PLACEHOLDER.findall(text))):
            raise ManifestError(f"{name} keeps the placeholders {', '.join(left)}")
        rendered[name] = text
    return rendered


def write(rendered: Mapping[str, str], target: Path) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in rendered.items():
        path = target / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="the released version, X.Y.Z")
    digest = parser.add_mutually_exclusive_group(required=True)
    digest.add_argument("--zip", type=Path, help="the release zip, hashed here")
    digest.add_argument("--sha256", help="the zip's SHA-256, if the file is not at hand")
    parser.add_argument("--url", help="installer URL (default: the GitHub Release asset)")
    parser.add_argument("--release-date", help="YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("-o", "--out", type=Path, default=ROOT / "dist" / "winget")
    args = parser.parse_args(argv)
    try:
        if args.zip is not None and not args.zip.is_file():
            raise ManifestError(f"no such file: {args.zip}")
        if args.zip is not None and args.zip.name != zip_name(args.version):
            raise ManifestError(f"{args.zip.name} is not {zip_name(args.version)}")
        sha = sha256_of(args.zip) if args.zip is not None else args.sha256
        rendered = render(args.version, sha, args.url, args.release_date)
    except ManifestError as exc:
        parser.error(str(exc))
    target = manifest_dir(args.out, args.version)
    for path in write(rendered, target):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
