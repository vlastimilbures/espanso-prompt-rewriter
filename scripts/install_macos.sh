#!/usr/bin/env bash
set -euo pipefail

# Contributor install: the CLI from this checkout, pinned to uv.lock, then the managed
# deploy (`prompt-workflow espanso deploy`) writes the match files into Espanso.
for arg in "$@"; do
  case "$arg" in
    --with-config)
      echo "--with-config was removed: Espanso's own config/default.yml is never replaced." >&2
      exit 1
      ;;
    *) echo "Unknown option: $arg (this script takes none)" >&2; exit 1 ;;
  esac
done

for tool in uv espanso; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "'$tool' was not found on PATH. Install it before running this script." >&2
    exit 1
  fi
done

repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_dir"

# `uv tool install` ignores uv.lock, so pass the locked versions as constraints (#34). The
# tool gets its own isolated environment; dev dependencies are not needed to deploy.
constraints="$(mktemp)"
trap 'rm -f "$constraints"' EXIT
uv export --frozen --no-dev --no-emit-project --no-hashes --quiet -o "$constraints"
uv tool install --editable . --force -c "$constraints"

# Ask uv where it installed the tool rather than `command -v`, which would pick up an
# activated project venv whose binary disappears if .venv is removed.
cli_path="$(uv tool dir --bin)/prompt-workflow"
tool_python="$(uv tool dir)/espanso-prompt-rewriter/bin/python"
if [ ! -x "$cli_path" ] || [ ! -x "$tool_python" ]; then
  echo "Could not locate prompt-workflow. Check 'uv tool install' output." >&2
  exit 1
fi
"$tool_python" scripts/check_tool_lock.py --python "$tool_python"

# The .env holds API keys: keep it readable by this user only.
if [ -f "$repo_dir/.env" ]; then
  chmod 600 "$repo_dir/.env"
fi

# Shows the plan, keeps any match file you edited (see `prompt-workflow espanso status`),
# writes the rest with this launcher, and restarts Espanso.
"$cli_path" espanso deploy --yes --launcher "$cli_path"
echo "Installed. Test -p- and -i- in any text field."
echo "Settings are read from $repo_dir/.env (copy .env.example), or from the file"
echo "named by PROMPT_WORKFLOW_ENV."
