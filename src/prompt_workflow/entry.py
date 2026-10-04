"""Console-script entry point (`prompt-workflow`).

Espanso runs the CLI as a `type: script` var, which treats any stderr output as a failure, so
warnings are silenced here, before the CLI and its dependencies are imported: an import-time
warning would otherwise turn every expansion into Espanso's generic error.
"""

from __future__ import annotations

import warnings


def main() -> None:
    warnings.simplefilter("ignore")
    from .cli import app

    app()
