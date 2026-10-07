"""The console-script entry point (`promptmend` and the `prompt-workflow` alias).

Espanso runs the triggers' calls as `type: script` vars (#18), and such a var fails on any
stderr output, even with exit code 0. So before the CLI and its dependencies are imported, a
trigger call (`improve`, `persona`) silences Python warnings: an import-time or run-time
warning would otherwise turn the expansion into Espanso's generic error. Every other command
keeps the default warning filters.
"""

from __future__ import annotations

import sys
import warnings

# The subcommands the Espanso match files run, always as the first argument.
TRIGGER_COMMANDS = frozenset({"improve", "persona"})


def quiet_trigger(argv: list[str]) -> bool:
    """Silence warnings when ``argv`` (without the program) runs a trigger command."""
    if argv[:1] and argv[0] in TRIGGER_COMMANDS:
        warnings.simplefilter("ignore")
        return True
    return False


def main() -> None:
    quiet_trigger(sys.argv[1:])
    from .cli import app

    app()


if __name__ == "__main__":
    main()
