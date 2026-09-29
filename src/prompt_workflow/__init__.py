from importlib.metadata import PackageNotFoundError, version

# Single source of truth is pyproject.toml; a hand-kept copy here drifted before.
try:
    __version__ = version("espanso-prompt-rewriter")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"
