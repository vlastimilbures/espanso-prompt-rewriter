"""Render README's flow diagram, docs/flow.mmd, into a light and a dark SVG (#200).

    python3 scripts/render_diagram.py           # needs npx (Node.js) and the network
    python3 scripts/render_diagram.py --browser PATH   # with an installed Chrome or Chromium
    python3 scripts/render_diagram.py --check   # only verifies the SVGs are current

Runs `npx -y @mermaid-js/mermaid-cli` twice (theme `default` into docs/flow-light.svg, theme
`dark` into docs/flow-dark.svg, transparent background, labels as SVG text, since GitHub
shows an SVG as an image, where HTML labels may not render), then writes the source's SHA-256
into each SVG as a comment, which `--check` and tests/test_docs.py compare with the source.
`--browser` renders with a Chrome already installed instead of the one Puppeteer downloads.
Rerun it after editing docs/flow.mmd. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "docs" / "flow.mmd"
OUTPUTS = {"default": REPO / "docs" / "flow-light.svg", "dark": REPO / "docs" / "flow-dark.svg"}
MERMAID_CLI = "@mermaid-js/mermaid-cli"
# Real <text> labels instead of <foreignObject> HTML.
CONFIG = {"htmlLabels": False, "flowchart": {"htmlLabels": False}}
_STAMP = re.compile(r"<!-- flow\.mmd sha256:([0-9a-f]{64}) -->\n?")
_XML_DECLARATION = re.compile(r"<\?xml[^>]*\?>\n?")


def digest(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def stamp(svg: str, hexdigest: str) -> str:
    """The SVG with its hash comment first (after an XML declaration, if any), an older
    comment replaced."""
    svg = _STAMP.sub("", svg).lstrip("\n")
    comment = f"<!-- flow.mmd sha256:{hexdigest} -->\n"
    declaration = _XML_DECLARATION.match(svg)
    if declaration:
        head = svg[: declaration.end()].rstrip("\n")
        return f"{head}\n{comment}{svg[declaration.end() :]}"
    return comment + svg


def stamped_digest(svg: str) -> str | None:
    found = _STAMP.search(svg)
    return found.group(1) if found else None


def stale(source: Path = SOURCE, outputs: dict[str, Path] = OUTPUTS) -> list[Path]:
    """The SVGs that are missing or do not carry the source's current hash."""
    expected = digest(source.read_bytes())
    return [
        path
        for path in outputs.values()
        if not path.is_file() or stamped_digest(path.read_text("utf-8")) != expected
    ]


def render(
    source: Path = SOURCE, outputs: dict[str, Path] = OUTPUTS, browser: Path | None = None
) -> None:
    npx = shutil.which("npx")
    if npx is None:
        raise OSError("npx not found: install Node.js")
    hexdigest = digest(source.read_bytes())
    env = dict(os.environ)
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.json"
        config.write_text(json.dumps(CONFIG), "utf-8")
        extra: list[str] = []
        if browser is not None:
            puppeteer = Path(tmp) / "puppeteer.json"
            puppeteer.write_text(json.dumps({"executablePath": str(browser)}), "utf-8")
            extra = ["-p", str(puppeteer)]
            env["PUPPETEER_SKIP_DOWNLOAD"] = "true"
        for theme, output in outputs.items():
            rendered = Path(tmp) / f"{theme}.svg"
            argv = [npx, "-y", MERMAID_CLI, "-i", str(source), "-o", str(rendered)]
            argv += ["-t", theme, "-b", "transparent", "-c", str(config), "-q", *extra]
            # No stdin: npx would otherwise wait on a prompt nobody sees.
            subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv, check=True, env=env, stdin=subprocess.DEVNULL
            )
            svg = rendered.read_text("utf-8")
            output.write_text(stamp(svg, hexdigest).rstrip("\n") + "\n", "utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only verify the SVGs' hash")
    parser.add_argument("--browser", type=Path, help="a Chrome or Chromium to render with")
    args = parser.parse_args(argv)
    if args.check:
        outdated = stale()
        if outdated:
            names = ", ".join(p.name for p in outdated)
            sys.exit(f"render_diagram: {names} out of date: run scripts/render_diagram.py")
        return
    try:
        render(browser=args.browser)
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"render_diagram: {exc}")


if __name__ == "__main__":
    main()
