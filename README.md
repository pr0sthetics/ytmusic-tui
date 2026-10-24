# 🎧 YouTube Music TUI

A **minimalist, fast and lightweight TUI client** for YouTube Music, right in your terminal: search, playback (mpv), music and album downloads (yt-dlp + ffmpeg), a local library and local playlists — **no Google account needed**.

**Website:** <https://pr0sthetics.github.io/ytmusic-tui/>

![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-710%20passing-brightgreen)
![CI](https://github.com/pr0sthetics/ytmusic-tui/actions/workflows/ci.yml/badge.svg)
![AI-native](https://img.shields.io/badge/AI--native-multi--model-blueviolet)

> ## 🤖 AI-native, human-verified
>
> This project is an **AI-native application**: the codebase, its architecture and its test suite are produced from high-level specifications through a **prompt-driven, multi-model workflow** — with the human author as architect, reviewer and integration tester every step of the way (see [Development methodology & AI stack](#-development-methodology--ai-stack)). It ships with **714 automated tests** (710 run offline by default) and real daily use, so it **actually works** — but it's pre-1.0: expect the occasional bug, code that "looks AI-written" and missing features (see **Limitations**).
> Found a bug? Open an [issue](https://github.com/pr0sthetics/ytmusic-tui/issues). PRs are very welcome! 💜

---

## ✨ Features

- 🎵 **Music playback** via mpv (direct streaming, selectable quality)
- 📻 **Automatic radio at the end of the queue**: when the last track ends (repeat off), playback continues with related popular tracks from YouTube Music's song radio; each track seeds the next, with session dedupe — no personalized home or generated playlist
- 🔍 **Search** for songs (25), videos (20) and albums (15) in parallel, with type and year on albums (`💿 My Dear Melancholy, — The Weeknd  (EP, 2018)`)
- 💿 **Album tab**: open an album from the search and see tracks, artist and year — `Enter` plays (with a continuous queue) and `A` downloads the whole album, **skipping tracks already downloaded**
- 📥 **Download** via yt-dlp + ffmpeg: best stream available → MP3 320 kbps (or opus/m4a/flac), with ID3 tags and **embedded cover art**
- 📚 **Local library** in SQLite (no cloud): downloaded tracks organized **by album** (plus a "standalone tracks" section), local likes and playback from the local file
- 🎵 **Local playlists** in SQLite: create, rename and delete (with confirmation), add a track from the library or from the search results, or a whole album from the library, reorder and play **in order**
- 📊 **Downloads** tab with real-time progress and configurable simultaneous downloads
- 🚫 **No login**: search, playback and download all operate anonymously
- ⌨️ Responsive **Textual** interface, with mouse support (tabs, lists, buttons) and vim-style shortcuts
- 🎨 **Themes** from the command palette: `Ctrl+P` (or the ⭘ button in the header) → **Theme** switches to any of Textual's built-in themes (Dracula, Nord, Gruvbox, Catppuccin, Solarized, Tokyo Night, dark/light variants); the choice applies to the current session
- ⚡ **Lightweight**: no Electron, no browser — just Python + mpv (~90 MB of memory at startup, proportional share; ~125 MB RSS)

## 📸 Screenshots

### Search — songs and albums in separate sections

```text
 ⭘                                      YouTube Music TUI                                  04:43:20
  🎵 Playlists  🔍 Busca    📚 Biblioteca    📥 Downloads    ⚙️ Config
━━━━━━━━━━━━━━╸━━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 ┌────────────────────────────────────────────────────────────────────────────────────────────────┐
 │  my dear melancholy                                                                            │
 └────────────────────────────────────────────────────────────────────────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                                                                                │
 │  🎵 Músicas                                                                                    │
 │ ┌────────────────────────────────────────────────────────────────────────────────────────────┐ │
 │ │ I Was Never There — The Weeknd, Gesaffelstein  (4:02)                                      │ │
 │ │ Try Me — The Weeknd  (3:42)                                                                │ │
 │ │ my dear melancholy — msht  (2:16)                                                          │ │
 │ │ Wasted Times — The Weeknd  (3:41)                                                        ▆▆│ │
 │ │ Hurt You — The Weeknd, Gesaffelstein  (3:51)                                               │ │
 │ │ TMJ (Acoustic Version) — MOHXSIN  (2:22)                                                   │ │
 │ │ Privilege — The Weeknd  (2:51)                                                             │ │
 │ │ my dear melancholy (slowed) — msht  (2:33)                                                 │ │
 │ │ Rockstar without guitar (Remix) — MOHXSIN  (3:10)                                          │ │
 │ └────────────────────────────────────────────────────────────────────────────────────────────┘ │
 │  💿 Álbuns                                                                                     │
 │ ┌────────────────────────────────────────────────────────────────────────────────────────────┐ │
 │ │ 💿 My Dear Melancholy, — The Weeknd  (EP, 2018)                                            │ │
 │ │ 💿 my dear melancholy — msht  (EP, 2026)                                                   │ │
 │ └────────────────────────────────────────────────────────────────────────────────────────────┘ │
 │                                                                                                │
 └────────────────────────────────────────────────────────────────────────────────────────────────┘

────────────────────────────────────────────────────────────────────────────────────────────────────
                                             ┌───┐ ┌───┐ ┌───┐            0:00   ━━━━━━━━━━━━━━━━━
                                             │ ⏮ │ │ ⏯ │ │ ⏭ │
                                             └───┘ └───┘ └───┘
```

### Album tab — `Enter` on a search result opens it here; `A` downloads the whole album

```text
 ⭘                                      YouTube Music TUI                                  04:43:21
  🎵 Playlists  🔍 Busca    💿 My Dear Melancholy    📚 Biblioteca    📥 Downloads    ⚙️ Config
━━━━━━━━━━━━━━━━━━━━━━━━━━╸━━━━━━━━━━━━━━━━━━━━━╺━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

 💿 My Dear Melancholy, — The Weeknd · 2018 · 6 faixas

 ┌────────────────────────────┐
 │ ⬇ Baixar álbum inteiro (a) │
 └────────────────────────────┘

 ┌────────────────────────────────────────────────────────────────────────────────────────────────┐
 │ Call Out My Name — The Weeknd  (3:49)                                                          │
 │ Try Me — The Weeknd  (3:42)                                                                    │
 │ Wasted Times — The Weeknd  (3:41)                                                              │
 │ I Was Never There — The Weeknd, Gesaffelstein  (4:02)                                          │
 │ Hurt You — The Weeknd, Gesaffelstein  (3:51)                                                   │
 │ Privilege — The Weeknd  (2:51)                                                                 │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 │                                                                                                │
 └────────────────────────────────────────────────────────────────────────────────────────────────┘

────────────────────────────────────────────────────────────────────────────────────────────────────
                                             ┌───┐ ┌───┐ ┌───┐            0:00   ━━━━━━━━━━╺━━━━━━
                                             │ ⏮ │ │ ⏯ │ │ ⏭ │
                                             └───┘ └───┘ └───┘
```

*(Screenshots of the real app, with live API data; colors vary with your terminal theme.)*

## 📋 Requirements

- **Python 3.11–3.13** — the supported range (3.14 is not validated yet; see *Compatibility*)
- **mpv** (with libmpv) — **required**: it's the player, the app won't start without it (on Ubuntu/Debian, `libmpv-dev` may be needed too — see *Common installation problems*)
- **ffmpeg** — required for downloads (conversion and tagging); search and playback work without it
- Python dependencies (ytmusicapi, textual, yt-dlp, sqlalchemy…) are installed via pip/pipx along with the app
- A terminal with colors (256/true color) and Unicode — **minimum 80×24**, 100×30+ recommended

## 🖥️ Compatibility

Levels are explicit — "works" below means what the project actually validates:

| Level | Platform | Status |
|-------|----------|--------|
| ⭐ **Reference** | **LMDE 7 / Linux Mint** | The platform the author uses daily, on two machines (Debian 13 base); everything is developed and tested here first |
| ✅ **Distribution** | **Ubuntu / Debian-compatible** | Same packaging base (Python 3.11–3.13, mpv + ffmpeg); CI builds, installs and runs the published artifacts on Ubuntu 24.04 |
| 🟡 **Compatibility** | **Other Linux distros** | Any distro with Python 3.11–3.13, mpv (with libmpv) and ffmpeg should work — untested by the project |
| 🖥️ **Interface** | **Terminal** | Colors (256/true color) and Unicode; **minimum 80×24**, 100×30+ recommended |
| ❌ **Not officially supported** | **macOS** | Might work with `brew install mpv ffmpeg` — a community path, not validated, nothing promised |
| ❌ **Not officially supported** | **Windows (WSL2)** | Runs inside WSL following the Linux instructions — not validated |
| ❌ **Not officially supported** | **Windows (native)** | Would require configuring the libmpv DLL manually |

## ✅ Known-good versions

Validated combinations (the exact Python dependency set used in CI is in [`uv.lock`](uv.lock); the floors are in `pyproject.toml`):

| Component | Validated version | How to check |
|---|---|---|
| **Python** | 3.11–3.13 — the supported range (3.11 + 3.13 in CI; 3.12 validated locally with the full suite; 3.14 not validated yet) | `python3 --version` |
| **mpv / libmpv** | mpv 0.40.0 with libmpv.so.2 (LMDE 7 / Debian 13) | `mpv --version \| head -n1` · `ldconfig -p \| grep libmpv` |
| **ffmpeg** | 7.1.5 (Debian 13) | `ffmpeg -version \| head -n1` |
| **yt-dlp** | floor `>=2024.1.0` in pyproject; 2026.8.19 in the author's environment | `yt-dlp --version` · in the venv: `python -c "import yt_dlp; print(yt_dlp.version.__version__)"` |
| **ytmusicapi** | floor `>=1.8.0` in pyproject; 1.12.3 in the author's environment (checked at runtime on the ⚙️ Config tab, T10) | in the venv: `pip show ytmusicapi \| head -n2` |

**yt-dlp** changes often (YouTube keeps changing the rules of the game): keep it up to date — Dependabot already opens a weekly PR, and an update that breaks playback shows up in CI before the merge.

## 📦 Installation

### 1. System dependencies

```bash
# Ubuntu / Debian / Linux Mint / LMDE
sudo apt install mpv ffmpeg python3-venv

# Arch / Manjaro / EndeavourOS
sudo pacman -S mpv ffmpeg python

# Fedora
sudo dnf install mpv ffmpeg python3

# openSUSE
sudo zypper install mpv ffmpeg python3

# macOS (not officially supported — community path)
brew install mpv ffmpeg python
```

In every family: `mpv` (with libmpv) is **mandatory** — the app won't start without it; `ffmpeg` is **required for downloads** (search and playback work without it); Python is required to run the app. `python3-venv` (Debian/Ubuntu) is only needed to create virtualenvs yourself (Option C) — the `pipx` package already depends on it.

### 2. Install the app

**Option A — pipx (recommended, isolated environment):**

```bash
pipx install git+https://github.com/pr0sthetics/ytmusic-tui
```

**Option B — pip** (only where `pip --user` is allowed):

```bash
pip install --user git+https://github.com/pr0sthetics/ytmusic-tui
```

> On distros where the system Python is **externally managed** — Debian 12+, Ubuntu 23.04+ (including Linux Mint and LMDE) — pip refuses `--user` installs by policy ([PEP 668](https://peps.python.org/pep-0668/)). There, use Option A (pipx) or a dedicated venv; the `pip` commands in the sections below have the same restriction.

**Option C — from source (for development):**

```bash
git clone https://github.com/pr0sthetics/ytmusic-tui
cd ytmusic-tui
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/ytmusic-tui    # or: .venv/bin/python main.py
```

### 3. Configuration (optional)

The app **works with no configuration at all**. If you want to tweak the player, downloads and interface:

```bash
mkdir -p ~/.config/ytmusic-tui
cp config.example.toml ~/.config/ytmusic-tui/config.toml
```

One thing that isn't a config file setting: the **theme** — switch it live in the command palette (`Ctrl+P` → **Theme**; it applies to the current session).

### 4. Updating and downgrading

**Updating** (installs from the git URL follow the default branch):

```bash
pipx upgrade ytmusic-tui                                                      # Option A (pipx)
pip install --user --upgrade git+https://github.com/pr0sthetics/ytmusic-tui    # Option B (pip)
```

If pipx reports "already at latest version" but you want a fresh install from the branch (the version number doesn't change between commits), force it:

```bash
pipx install --force git+https://github.com/pr0sthetics/ytmusic-tui
```

**Downgrading — pin an exact version** (any tag, e.g. `v0.1.3`):

```bash
pipx install --force git+https://github.com/pr0sthetics/ytmusic-tui@v0.1.3
pip install --user --force-reinstall git+https://github.com/pr0sthetics/ytmusic-tui@v0.1.3
```

Tags are listed at [pr0sthetics/ytmusic-tui/tags](https://github.com/pr0sthetics/ytmusic-tui/tags). A tag-pinned install stays pinned — `pipx upgrade` won't move it; to go back to the latest, reinstall without the tag (see *Updating*).

### 5. Uninstalling

```bash
pipx uninstall ytmusic-tui     # Option A (pipx)
pip uninstall ytmusic-tui      # Option B (pip)
```

Uninstalling removes **only the app** (code + launcher). Your **local library and downloads are NOT removed** — they stay in place, ready for a possible reinstall.

### Removing local data (only if you want to)

The app never deletes any of this on its own. To remove everything:

| What | Path (Linux) | Remove with |
|-------|--------------|-------------|
| Configuration | `~/.config/ytmusic-tui/` | `rm -rf ~/.config/ytmusic-tui` |
| Library (SQLite + backups) | `~/.local/share/ytmusic-tui/` | `rm -rf ~/.local/share/ytmusic-tui` |
| Cache | `~/.cache/ytmusic-tui/` | `rm -rf ~/.cache/ytmusic-tui` |
| Logs | `~/.local/state/ytmusic-tui/` | `rm -rf ~/.local/state/ytmusic-tui` |
| Downloads | `~/Music/YouTube Music/` | `rm -rf "$HOME/Music/YouTube Music"` |

### Common installation problems

- **`Cannot find libmpv in the usual places`** (or a similar python-mpv message) when opening the app: Python couldn't find libmpv. On Ubuntu/Debian also install `sudo apt install libmpv-dev` — it provides the `libmpv.so` symlink Python looks for (on Ubuntu 24.04, for example, the `mpv` package alone is not always enough).
- **Music doesn't play / the stream fails**: it's almost always yt-dlp out of date with some YouTube change — update the app and yt-dlp in the same install environment (`pip install -U yt-dlp` in the venv, or `pipx upgrade` + `pipx runpip ytmusic-tui install -U yt-dlp`).
- **`pipx` inside a container (distrobox/toolbox) sharing `$HOME`**: with a shared HOME, pipx writes its venv and metadata into the **host's** pipx (`~/.local/share/pipx`), but built with the **container's** Python — once the container is gone you are left with an orphaned venv and stray launchers in `~/.local/bin`. Rule: **run pipx in the same environment where you want the app installed**. Inside a container/toolbox, isolate pipx instead:

  ```bash
  # inside the container/toolbox: keep pipx state and launchers out of the host's
  export PIPX_HOME="$HOME/.local/share/pipx-ytmusic"
  export PIPX_BIN_DIR="$HOME/.local/bin-ytmusic"
  pipx install git+https://github.com/pr0sthetics/ytmusic-tui
  # the launcher lands in $PIPX_BIN_DIR — add that directory to your PATH
  ```

## 🚀 Usage

```bash
ytmusic-tui              # installed via pip/pipx
python -m ytmusic_tui    # as a module
python main.py           # straight from the source tree (uses src/, no install)
```

`ytmusic-tui --version` prints the installed version (handy in bug reports) and `ytmusic-tui doctor` checks the environment without opening the interface.

`ytmusic-tui` (console script) and `python -m ytmusic_tui` are both **supported entry points** and equivalent, `doctor` included; `python main.py` only works from a source checkout.

### Basic flow

1. `Ctrl+S` to go to the search → type and press `Enter`
2. `Enter` on a song (or video) plays it; `p` adds the highlighted result to a playlist (it must already be downloaded); `→` / `←` move through the queue
3. `Enter` on an album opens the 💿 tab with its tracks; `A` (or the button) downloads the whole album
4. `Ctrl+D` downloads the track now playing — follow it on the 📥 Downloads tab
5. `Ctrl+L` opens the local library — now **by album**: `Enter` expands an album (tracks in order; `t` plays it), `p` adds the selected track and `Shift+P` adds the whole album (open or just highlighted) to a playlist
6. The 🎵 **Playlists** tab (the first one) lists your local playlists: `Enter` opens one (tracks in position order; `Enter` plays with the queue following the playlist), `N` creates, `E` renames, `Delete` deletes with confirmation; inside a playlist, `Shift+↑`/`Shift+↓` reorder and `Delete` removes a track

### Keyboard shortcuts

| Key | Action |
|-------|------|
| `Ctrl+S` | Go to the search |
| `Enter` | Play track / open selected album; library: expand album; playlists: open playlist |
| `Space` | Play/Pause |
| `→` / `←` | Next / previous (inside a library album or an open playlist, `←` goes back one level) |
| `Ctrl+D` | Download the track now playing |
| `A` | Download the whole album (album tab) |
| `T` | Play the open album from the library |
| `P` | Add the selected library track, the highlighted search result, or the current track to a playlist |
| `Shift+P` | Add the open or highlighted library album to a playlist |
| `N` / `E` | Playlists: new / rename |
| `Delete` | Delete the selected playlist (with confirmation); inside a playlist, remove the selected track |
| `Shift+↑` / `Shift+↓` | Move the selected track inside an open playlist |
| `Esc` | Back from the album to the search; back one level in the library/playlist |
| `L` | Like / unlike (local, for downloaded tracks) |
| `Ctrl+L` | Go to the library |
| `F1` | Help |
| `Ctrl+P` | Open the command palette — theme, keys, screenshot |
| `Ctrl+Q` | Quit |

The mouse works too: click on tabs, results and buttons.

## ⚙️ Configuration

File: `~/.config/ytmusic-tui/config.toml` (every key is optional).
The app's **Config** tab edits the main options and writes to that same file right away.

| Key | Default | Description |
|-------|--------|-----------|
| `mpv_volume` | `50` | Initial volume (0–100) |
| `mpv_audio_quality` | `"high"` | Stream used by mpv: `low`, `medium`, `high`, `best` |
| `download_dir` | `"~/Music/YouTube Music"` | Downloads directory |
| `download_format` | `"mp3"` | Format: `mp3`, `opus`, `m4a` or `flac` |
| `download_quality` | `"320"` | Bitrate in kbps (mp3/opus) or `"best"` |
| `download_concurrent` | `3` | Simultaneous downloads (1–10) |
| `embed_metadata` | `true` | ID3 tags + embedded cover art in the files |
| `show_notifications` | `true` | Toast notifications |
| `auto_play_next` | `true` | Automatically play the next track in the queue |
| `auto_similar` | `true` | At the end of the queue, continue with related tracks (radio; requires `auto_play_next`) |
| `library_db_name` | `"library.db"` | SQLite file name of the local library |
| `config_dir`, `data_dir`, `cache_dir` | XDG | Internal directories (advanced, usually don't touch) |

Environment variables prefixed with `YTMUSIC_` take priority over the TOML (handy for isolated testing):

```bash
YTMUSIC_DOWNLOAD_DIR=/tmp/teste YTMUSIC_DATA_DIR=/tmp/teste ytmusic-tui
```

### Where the files live (Linux)

| What | Path |
|-------|---------|
| Configuration | `~/.config/ytmusic-tui/config.toml` |
| Library (SQLite) | `~/.local/share/ytmusic-tui/library.db` |
| Cache | `~/.cache/ytmusic-tui/` |
| Downloads | `~/Music/YouTube Music/` |
| Log | `~/.local/state/ytmusic-tui/log/ytmusic-tui.log` |

*(On macOS/Windows the equivalent `platformdirs` paths are used.)*

Internal errors (for example, failing to reach the library database) show up
in the interface itself **and** in the app log — check the two sections below
before opening an issue.

### Logs (`~/.local/state/ytmusic-tui/log/ytmusic-tui.log`)

The log is **JSON Lines**: each line is a JSON object with fixed fields (`ts`,
`level`, `component`, `operation_id`, `event`, `error_code`, `message` and, on
errors, `detail` with the traceback). Important events — app start/stop (with
the versions), download started/finished/failed, API retries, database
migrations, database errors, quarantine and recovery — carry their own `event`
and `error_code`, which makes diagnosis greppable:

```bash
jq -c 'select(.event != null)' ~/.local/state/ytmusic-tui/log/ytmusic-tui.log | tail
```

Privacy is enforced by the app itself when writing: HOME becomes `~`, URLs
lose query/token and credential headers become `[REDACTED]` — none of that
stays raw in the file. The log is 0600, with 1 MB × 3 backups rotation
(4 MB maximum in total).

### Diagnostics (`ytmusic-tui doctor`)

The `ytmusic-tui doctor` command (or `python -m ytmusic_tui doctor`) checks
the environment **without opening the interface** — the checks do not create,
change or download any files: versions (app, Python, ytmusicapi, yt-dlp),
libmpv and the `mpv`/`ffmpeg` binaries, directories (existence, writability,
mode), the library database read-only (`integrity_check` + schema version ×
app) and maintenance leftovers (quarantine, backups, log).

```bash
ytmusic-tui doctor
```

The output is masked (same rules as the log) and made to be pasted into an
issue — still, review it before sharing. Exit code `0` = nothing essential
broken; `1` = something essential (libmpv or a dependency missing, a
directory without write access, a database unreadable/corrupted or newer
than the app).

## 🔧 How it works (under the hood)

- **[ytmusicapi](https://github.com/sigma67/ytmusicapi)** (unofficial) in **anonymous mode** for search and metadata — the search fires requests in parallel and takes ~2s
- **[mpv](https://mpv.io/)** (via libmpv/python-mpv) plays the streams; mpv uses the **yt-dlp installed alongside the app** as its extractor (`ytdl_hook-ytdl_path`), so playback follows the up-to-date yt-dlp instead of mpv's old built-in extractor
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp) + ffmpeg** download the best audio stream and convert it to the configured format, with cover art and tags
- **SQLAlchemy + SQLite** hold the local library (downloaded tracks, likes, local playlists)

> **Why no login?** Google turned off ytmusicapi's OAuth authentication in 2025 — tokens started being rejected with HTTP 400 ([sigma67/ytmusicapi#813](https://github.com/sigma67/ytmusicapi/issues/813)). Since search, playback and download work anonymously, this client chose to operate **without an account**: no synchronization, everything local.
>
> ⚙️ Technical detail: libmpv requires `LC_NUMERIC=C`, but ytmusicapi switches the locale to `en_US.UTF-8` (and mpv aborted because of that). The app restores the locale before starting the player.

## ⚠️ Limitations

**Account and library:**
- No login: likes, **account** playlists and recommendations don't show up; the `L` "like" is **local** and only works for tracks already downloaded — the playlists in the 🎵 tab are local too (they live in your `library.db`)
- There's no synchronization with YouTube Music — the library lives only in your `library.db`

**Search and catalog:**
- Up to **25 songs + 20 videos + 15 albums** per query
- Video results (clips/lives) come as **audio**: they play and download like songs — there's no video image
- Adding to a playlist from the search needs the track **downloaded** first (`p` shows the way when it isn't)
- No advanced filters (search by artist, playlist, sorting etc.)

**Downloads and quality:**
- The real quality depends on the source: YouTube Music typically serves **~128–256 kbps** audio (AAC/Opus); the "MP3 320" is a conversion — it doesn't create quality, but it standardizes the files
- Downloads depend on yt-dlp; when YouTube changes something, updating yt-dlp in the same environment (or reinstalling/updating the app) usually fixes it

**Young project:**
- Interface and messages **in Portuguese**
- **AI-native, human-verified** (note at the top): the 710 offline tests cover the main flow, but the occasional bug is expected
- No MPRIS, lyrics or equalizer (Roadmap). Automatic radio continues the queue with related tracks; it is not a personalized home or a generated playlist. Built-in **themes** are available from the command palette and apply to the current session; custom theme files are not supported yet.

## 🆘 When something breaks (and it's not the app)

Not every failure is a bug here: the app depends on external services and
tools that change all the time. Short checklist, from the cheapest step to
the most specific:

1. **First step: `ytmusic-tui doctor`.** It checks versions, libmpv,
   `mpv`/`ffmpeg`, directories and the library database read-only, and the
   output is already masked to paste into an issue. Exit `0` = nothing
   essential broken; exit `1` = something essential is off (the report says
   what). More in *Logs* and *Diagnostics* above.
2. **The YouTube Music contract changed.** Symptoms: structure/field errors
   when searching or opening an album, or a red contract run (the weekly
   `contract.yml` workflow). From a source checkout, run the real-integration
   tests and attach the per-field/per-item report to the issue:
   `YTMUSIC_INTEGRATION=1 .venv/bin/python -m pytest tests/test_integration.py -v -s`.
   A green `doctor` with a red contract run points outside: the service
   changed, not your install.
3. **yt-dlp extraction broke** (YouTube changed the rules of the game).
   Symptoms: downloads fail, or the log shows a yt-dlp error or an
   `FFMPEG_EXIT_N` error code. Update yt-dlp in the same install environment
   (`pip install -U yt-dlp` in the venv, or
   `pipx runpip ytmusic-tui install -U yt-dlp`) and watch for the Dependabot
   PR; yt-dlp's own issue tracker usually tells whether it's a known YouTube
   change.
4. **Network, rate limit, block or service down.** Timeouts, 5xx, 403, "are
   you a robot", rate limiting, region restrictions: try again later —
   inconclusive is neither corrupted data nor a bug.
5. **mpv/libmpv or ffmpeg missing.** Symptoms: the player doesn't open, or
   the downloads' conversion fails. `doctor` flags it; install the system
   packages from *Installation* above (`mpv` — plus `libmpv-dev` on
   Ubuntu/Debian — and `ffmpeg`).
6. **How to report:** the `doctor` output + a log excerpt + the versions
   involved. The log is already masked (no tokens, signed URLs or
   credentials), so you don't need to censor it by hand.

And a reminder: the contract check runs **every Monday** (`contract.yml`) —
a red run there is an external problem, not your installation.

## 🗂️ Project structure

```text
ytmusic-tui/
├── main.py                    # Shortcut to run from source (uses src/ directly)
├── pyproject.toml             # Metadata and dependencies (pip/pipx)
├── config.example.toml        # Commented configuration template
├── src/ytmusic_tui/
│   ├── main.py                # Entry point of the `ytmusic-tui` console script
│   ├── config/                # Configuration (pydantic-settings + TOML persistence)
│   ├── api/                   # Anonymous ytmusicapi (search, albums)
│   ├── player/                # mpv player (libmpv) + yt-dlp integration
│   ├── download/              # yt-dlp download queue (progress, concurrency)
│   ├── library/               # SQLite library (SQLAlchemy)
│   ├── log.py                 # File logging (with rotation)
│   └── tui/                   # Textual interface
│       ├── app.py             # App lifecycle + main() (app.tcss next to it)
│       ├── screens.py         # MainScreen: tabs, shortcuts and navigation
│       ├── widgets.py         # Custom widgets (lists, player bar)
│       └── *_controller.py    # Playback, downloads, library and playlists (mixins)
└── tests/                     # 710 offline tests in layers (L1 mocks + L2 local) + 4 opt-in
```

## 🤖 Development methodology & AI stack

This is an **AI-native application**: instead of hand-typing the code, the architecture, the implementation and the tests are **generated from high-level specifications and natural-language directives**, through a prompt-driven, multi-model workflow. The engineering discipline around it is the same one you would expect from a human team — every task comes from a written backlog with acceptance criteria, every change ships with tests, and a red CI run is a stop-the-line event: fixed forward on a new commit, never by rewriting history.

### The AI ensemble

Different models, each used for what it does best:

- **GPT-6 Sol** — architecting and reasoning: system design, complex logic and algorithmic decisions.
- **DeepSeek v4.1 Flash** and **Gemini 3.8 Flash** — rapid iteration and scripting: boilerplate, structural Python, utilities, test implementation and refactors.

### Human–AI synergy

- **Human author** — system architect, prompt engineer, code reviewer and integration tester: owns the specifications and the backlog, reviews every change, validates by hand in real daily use and makes the go/no-go call on every release.
- **AI ensemble** — continuous code generation, automated test implementation and refactoring, always inside a task's acceptance criteria and gated by review plus CI.

### How quality is enforced

- **Layered testing** (details in **Development** below): hermetic offline unit/integration layers, opt-in real-network contract checks (plus the weekly scheduled run) and a controlled end-to-end flow — including a real libmpv player suite in CI.
- **CI on every push**: `ruff` + the offline suite on Python 3.11 and 3.13; a `pip-audit` security job over the locked dependency set; and a packaging job that installs the wheel and the sdist in clean environments and runs `doctor`.
- **Releases are deliberate**: manual, from a tagged commit with green CI — nothing is published automatically.

Expect pre-1.0 rough edges — the occasional bug and code that "looks AI-written" — but the app is exercised **daily on a real music library**, and the numbers here are honest.

## 🛠️ Development

```bash
git clone https://github.com/pr0sthetics/ytmusic-tui
cd ytmusic-tui
uv sync --locked --extra dev    # creates/updates .venv from uv.lock

# Tests (offline, everything mocked — L1 + L2)
.venv/bin/python -m pytest tests/ -q

# Branch coverage for download/library/player (informative, no threshold)
.venv/bin/python -m pytest tests/ -q \
  --cov=ytmusic_tui.download --cov=ytmusic_tui.library --cov=ytmusic_tui.player \
  --cov-branch --cov-report=term

# Lint
.venv/bin/ruff check src/ tests/

# Formatting
.venv/bin/ruff format src/ tests/

# Real integration tests (they talk to the real YouTube Music; disabled by default)
YTMUSIC_INTEGRATION=1 .venv/bin/python -m pytest tests/test_integration.py -v -s

# Vulnerability audit of the locked set (the same one CI audits)
uv export --frozen --no-emit-project --extra dev --no-hashes -o /tmp/ytm-locked.txt
.venv/bin/pip-audit -r /tmp/ytm-locked.txt
```

**[uv](https://docs.astral.sh/uv/)** is the development/CI tool: `uv.lock` pins the exact runtime + dev versions and CI installs from it (`uv sync --locked`). If `pyproject.toml` and `uv.lock` drift apart, `--locked` fails on purpose — after editing dependencies, run `uv lock`. No uv? `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"` still works (without the lockfile — CI is what uses it).

The tests cover the TUI (via Textual's `run_test`), API, player, downloads and library, in two offline layers: **L1** (unit/behavior, with thin mocks) and **L2** (`tests/integration_local/` — local integration with the worker, reservation/temporary files and a real database, plus "real" fakes: yt-dlp stubbed at the late import and an **executable** ffmpeg on PATH). **No test in the standard suite touches the network or the real player** — L2 blocks `socket` on purpose, so any connection fails immediately with a clear message; branch coverage (command above) is informative, with no threshold.

Two more test layers are **opt-in**, never run in CI:

- **L3 — real integration** (`tests/test_integration.py`): talks to the real YouTube Music and compares the raw service response with the contract the adapters consume, producing a per-field and per-item report when the format changes (run with `-s` to see the report). It also runs **every Monday at 06:00 UTC** in the `contract.yml` workflow (`workflow_dispatch` runs it on demand). Zero-noise rule: **inconclusive** (offline, service down, runner blocked) makes the tests skip with an explicit reason and the run stays green; a **contract break** fails with the report and the run turns red — the useful signal.
- **L4 — controlled end-to-end** (`tests/e2e/`, opt-in with `YTMUSIC_E2E=1`): builds the wheel, installs it in a clean venv and runs the real flow (doctor → search → album → player → download) fully isolated in tmp. Runbook in `tests/e2e/README.md`.

The **CI (GitHub Actions)** runs `ruff` + the offline suite on Python 3.11 and 3.13 on every push, **installing from `uv.lock`** (`uv sync --locked` — the job fails if the lockfile is missing or out of sync with `pyproject.toml`), a security job that exports the locked set and runs `pip-audit` (fails on a known vulnerability) and a package job that checks the wheel/sdist contents, installs **both** in clean environments and runs `doctor` (console script and `python -m ytmusic_tui`). Branch coverage (download, library and player) runs in the test job as an **informative** step — no threshold and no CI blocking —, on a single leg (Python 3.13).

Dependencies are updated by **Dependabot** (weekly PRs, dev/runtime groups — with the lockfile, it updates `uv.lock` and `pyproject.toml` together): CI runs on every PR, so an update that breaks the app is visible there before it lands — handy for a project that depends on external services that change often (YouTube, mpv, yt-dlp).

### Releases and updates

Each release corresponds to a **traceable commit** with the version in `pyproject.toml` and an entry in `CHANGELOG.md`, an annotated tag (`vX.Y.Z`) and a **green CI on the commit** (e.g.: `v0.1.2` → commit `84b6d80`). Releases are **manual** — nothing is published automatically: the release workflow (guarded by a green CI) builds the wheel and the sdist, verifies `--version` and `doctor` in clean environments and publishes them with a **`SHA256SUMS`** file.

Dependency updates follow a single path: **Dependabot opens the PR → mandatory CI (3.11/3.13 + lockfile + pip-audit + package) → manual merge**. Merging is not releasing: no update goes straight into a stable release.

Looking to update a **local installation** (pipx/pip), pin an older version or uninstall? That's covered in *Installation → Updating and downgrading*.

## 🗺️ Roadmap

- [ ] Synced lyrics
- [ ] MPRIS control (desktop integration)
- [ ] Equalizer
- [ ] Custom themes
- [ ] Last.fm / ListenBrainz integration

## 📄 License

MIT — see [LICENSE](LICENSE) for details. © 2026 Eeyore.

## 💜 Credits

- [ytmusicapi](https://github.com/sigma67/ytmusicapi) — unofficial YouTube Music API
- [Textual](https://github.com/Textualize/textual) — TUI framework
- [mpv](https://mpv.io/) — media player
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — audio download
- [ffmpeg](https://ffmpeg.org/) — conversion and metadata
- [Rich](https://github.com/Textualize/rich), [SQLAlchemy](https://www.sqlalchemy.org/) and [pydantic](https://docs.pydantic.dev/) — the rest of the stack
