<h1 align="center">gistgrep</h1>

<p align="center"><strong>Search your GitHub gists at the speed of grep — with AI summaries.</strong></p>

<p align="center">
  <img src="docs/demo.gif" alt="gistgrep demo" width="720">
</p>

## Why gistgrep?

GitHub's search API doesn't index gists. Its web UI can't search private gists. `gh gist list --include-content --filter "term"` works — but on 446 gists it takes **27 seconds** because it's N serial API calls.

`gistgrep` mirrors your gists locally, auto-syncs via a one-call freshness check, ranks results intelligently, and drops you into an `fzf` TUI with live previews. On macOS 26+ it also generates on-device AI summaries for every gist using Apple Intelligence — free, private, offline.

### Speed

| | 446 gists, search "ait" |
|---|---|
| GitHub web UI | Can't search private gists |
| `gh gist list --include-content --filter` | **27.4s** |
| `gistgrep --no-sync` | **0.08s** |
| `gistgrep` (with freshness check) | **0.3s** |

## Install

```bash
brew install mattheworiordan/tap/gistgrep
```

Or via curl:

```bash
curl -fsSL https://raw.githubusercontent.com/mattheworiordan/gistgrep/main/install.sh | bash
```

Both install to `~/.local/bin/gistgrep`. Make sure that's on your `PATH`.

## Quick start

```bash
gistgrep                    # interactive TUI over all your gists
gistgrep ait                # open TUI pre-filtered to "ait"
gistgrep --plain ait        # plain-text output (scriptable)
gistgrep --doctor           # verify dependencies
```

### TUI key bindings

- **Enter** — open gist in browser
- **Ctrl-Y** — copy gist URL to clipboard
- **Ctrl-E** — open cached copy in `$EDITOR`
- **Esc** — quit

### Ranking

Results are scored by:
- **Title / description**: ×10 per match
- **Filename**: ×5 per match
- **File body**: ×1 per match
- **Recency boost**: up to +5 for very recent gists, decaying over 180 days

Type into the fzf prompt to narrow further with fuzzy matching.

## How it works

| Layer | What |
|-------|------|
| **Local mirror** | Every gist's metadata + files cached under `~/.cache/gistgrep/` |
| **Freshness check** | One `/gists?per_page=1` call (~200ms) decides if anything upstream changed |
| **Incremental sync** | `/gists?since=<timestamp>` fetches only updated gists |
| **Deletion reconcile** | Weekly, in the background — fetches last 100 gists, prunes any locally-cached ones that disappeared |
| **AI summaries** | Apple Intelligence (`FoundationModels` framework) via a small embedded Swift helper, compiled on first use |
| **Summaries are async** | Written in the background after sync; never block search |
| **Interactive UI** | `fzf` with a custom preview pane |

All state lives in `~/.cache/gistgrep/`. Delete it to start fresh.

## Requirements

- **macOS** (tested on 14+; 26+ for AI summaries)
- `gh` CLI, authenticated (`gh auth login`)
- `fzf` (for the interactive UI — `brew install fzf`)
- Xcode Command Line Tools (`xcode-select --install`) — only if you want AI summaries; used to compile the tiny Swift helper that bridges to Apple Intelligence

Run `gistgrep --doctor` to check.

### Graceful degradation

`gistgrep` degrades gracefully:

- No `gh` → won't run. Hard stop.
- No `fzf` → falls back to plain-text output automatically.
- No Apple Intelligence (older macOS, or disabled) → search still works; a subtle warning replaces the summary line in the preview.

## What gistgrep is not

- Not a daemon. No background services. The tool self-heals its own cache on invocation.
- Not a web UI. Just a CLI/TUI.
- Not cross-platform. macOS only today — mostly because of the Apple Intelligence bridge. A Linux port could swap in Ollama or MLX; contributions welcome.

## Contributing

PRs welcome. Particularly interested in:

- **Linux / Windows ports** — the trickiest bit is the LLM bridge. Ollama or MLX are the obvious swaps.
- **Streaming summarization** — generate the summary visibly in the preview pane.
- **Performance** — parallel summarization (currently serial to avoid thrashing the model).

```bash
git clone https://github.com/mattheworiordan/gistgrep.git
cd gistgrep
./bin/gistgrep --doctor
```

## License

MIT — [Matthew O'Riordan](https://github.com/mattheworiordan)
