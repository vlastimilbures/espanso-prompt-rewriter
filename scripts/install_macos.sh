#!/usr/bin/env bash
set -euo pipefail

# --with-config also deploys espanso/config/default.yml, which sets toggle_key and
# search_shortcut. Off by default so an existing Espanso setup is left alone.
with_config=false
for arg in "$@"; do
  case "$arg" in
    --with-config) with_config=true ;;
    *) echo "Unknown option: $arg (supported: --with-config)" >&2; exit 1 ;;
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

# The tool gets its own isolated environment; dev dependencies are not needed to deploy.
uv tool install --editable . --force

# Resolve the absolute CLI path. GUI-launched Espanso does not inherit shell PATH.
# Ask uv where it installed the tool rather than `command -v`, which would pick up an
# activated project venv whose binary disappears if .venv is removed.
cli_path="$(uv tool dir --bin)/prompt-workflow"
if [ ! -x "$cli_path" ]; then
  echo "Could not locate prompt-workflow binary. Check 'uv tool install' output." >&2
  exit 1
fi
# The path goes inside a double-quoted YAML string that the shell runs; refuse characters
# the shell or YAML would interpret there, rather than try to escape them.
case "$cli_path" in
  *[\"\$\`\\]*)
    echo "CLI path contains a quote, \$, backtick or backslash: $cli_path" >&2
    exit 1
    ;;
esac
echo "Using CLI at: $cli_path"

# 'espanso path config' prints just the config dir (no 'Config:'/'Runtime:' label),
# unlike 'espanso path' which prints labelled lines for config/packages/runtime.
espanso_dir="$(espanso path config)"
mkdir -p "$espanso_dir/match"
stamp="$(date +%Y%m%d%H%M%S)"

backup() {
  cp "$1" "$1.bak-$stamp"
  echo "Backed up existing $1 to $1.bak-$stamp"
}

# Move $1 to $2, first backing up an existing $2 whose content differs, so a file the
# user edited (or Espanso's own default.yml) is never lost.
install_file() {
  if [ -f "$2" ] && ! cmp -s "$1" "$2"; then
    backup "$2"
  fi
  mv "$1" "$2"
}

# Before 0.9 the -p- template shipped as match/base.yml, the file Espanso itself creates
# for the user's own snippets. Retire our copy (backed up) so -p- is not defined twice,
# and leave any other base.yml alone.
legacy="$espanso_dir/match/base.yml"
if [ -f "$legacy" ] && grep -q 'trigger: "-p-"' "$legacy" && grep -q 'prompt-workflow' "$legacy"; then
  backup "$legacy"
  rm "$legacy"
fi

# Deploy match files, substituting the absolute CLI path placeholder.
# Uses python's str.replace (not sed -e) since cli_path may contain
# characters like '|' or '&' that break a sed substitution.
for f in espanso/match/*.yml; do
  target="$espanso_dir/match/$(basename "$f")"
  python3 -c '
import sys
text = open(sys.argv[1], encoding="utf-8").read()
text = text.replace("__PROMPT_WORKFLOW__", sys.argv[2])
open(sys.argv[3], "w", encoding="utf-8").write(text)
' "$f" "$cli_path" "$target.tmp"
  install_file "$target.tmp" "$target"
done

# Only with --with-config: Espanso's default.yml hard-sets toggle_key/search_shortcut
# the user may have customized.
if [ "$with_config" = true ]; then
  mkdir -p "$espanso_dir/config"
  for f in espanso/config/*.yml; do
    target="$espanso_dir/config/$(basename "$f")"
    cp "$f" "$target.tmp"
    install_file "$target.tmp" "$target"
  done
fi

espanso restart || espanso start
if [ "$with_config" = false ]; then
  echo "Left Espanso's config/default.yml untouched (pass --with-config to deploy ours)."
fi
echo "Installed. Test -p- and -i- in any text field."
echo "Settings are read from $repo_dir/.env (copy .env.example), or from the file"
echo "named by PROMPT_WORKFLOW_ENV."
