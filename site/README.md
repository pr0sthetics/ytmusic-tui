# ytmusic-tui — official site

Static, one-page site for [ytmusic-tui](https://github.com/pr0sthetics/ytmusic-tui). No build step, no runtime dependencies, no external requests. The design is a printed Unix field manual: cool paper, black ink, printer's blue rules and an oxblood stamp for caveats.

## Preview locally

From the repository root:

```bash
python3 -m http.server -d site 8000
```

Then open <http://localhost:8000/>. Opening `site/index.html` directly from the filesystem also works; copying uses the selection fallback when the Clipboard API is unavailable on `file://`.

**Font note:** Chromium-based browsers block local font files over `file://`
(CORS policy), so the page falls back to Georgia and the system sans there. Use
the HTTP server for the intended Libre Franklin, Source Serif 4 and Courier Prime
typography; Firefox may load the local fonts from `file://`.

## Publish to GitHub Pages

The site is deployed by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) with GitHub Actions. One-time setup:

1. Push `site/` and the workflow to `main`.
2. Open **Settings → Pages**.
3. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. Push a change under `site/` (or run the `pages` workflow manually). It publishes the static `site/` directory as-is to <https://pr0sthetics.github.io/ytmusic-tui/>.

Do **not** switch the Pages source to a branch folder: GitHub only accepts `/` (root) or `/docs` there, and the site lives in `site/`. No build step is required. The files use relative paths (`assets/...`), so the site works from a project subpath; do not add a leading slash to asset URLs.

## Domain placeholders

The current canonical URL is a placeholder: **`https://pr0sthetics.github.io/ytmusic-tui/`**. Replace it everywhere listed below if the site moves to a custom domain, or if the repository is renamed. The README is the single reference for this list.

- `index.html` — `<link rel="canonical">`
- `index.html` — `og:url`
- `index.html` — `og:image` (absolute URL)
- `index.html` — `twitter:image` (absolute URL)
- `index.html` — JSON-LD `url`
- `robots.txt` — `Sitemap:`
- `sitemap.xml` — `<loc>`

The Open Graph image is `assets/og.png` and must remain an absolute URL. If the site is served from a user/organization root instead of `/ytmusic-tui/`, update those URLs accordingly.

## Updating the version and numbers

The authoritative block is the HTML comment near the top of `index.html` (`VERSION AND COUNTS — single source of truth`). On a release, update:

- the comment block itself;
- the running-head version link (`.version`), its release URL, and the colophon version;
- the diagnostics ledger (`Release`, `Tests`, `CI`, `License`, `Distribution`);
- `meta`, Open Graph, Twitter and JSON-LD copy if they mention a release;
- JSON-LD `softwareVersion` (currently `"0.3.1"`);
- the version in the Open Graph source and the exported PNG.

Nothing is fetched from the GitHub API at runtime, and there are no star counters or analytics.

## Editing images

`assets/og.source.html` is the editable source for `assets/og.png` (1200×630). It uses the local fonts and no external assets. Regenerate it with any headless browser. With Firefox:

```bash
mkdir -p /tmp/firefox-profile /tmp/firefox-home
HOME=/tmp/firefox-home MOZ_HEADLESS=1 firefox --headless --no-remote \
  -profile /tmp/firefox-profile --window-size=1200,630 \
  --screenshot "$PWD/site/assets/og.png" \
  "file://$PWD/site/assets/og.source.html"
```

Keep the PNG under 300 KB. `assets/og.source.html` is not linked from the page and is marked `noindex`.

`assets/favicon.svg` is the source of the mark. `assets/apple-touch-icon.png` is a 180×180 export of that mark on the paper ground; its editable source is `assets/apple-touch-icon.source.html`. The exported PNGs carry provenance metadata in their PNG text chunks.

## Files

```text
site/
├── index.html
├── assets/
│   ├── styles.css
│   ├── main.js
│   ├── favicon.svg
│   ├── apple-touch-icon.png
│   ├── apple-touch-icon.source.html
│   ├── og.png
│   ├── og.source.html
│   └── fonts/
│       ├── courier-prime-400-normal.woff2
│       ├── courier-prime-700-normal.woff2
│       ├── libre-franklin-400-700-normal.woff2
│       ├── source-serif-4-400-normal.woff2
│       ├── source-serif-4-600-normal.woff2
│       ├── source-serif-4-400-italic.woff2
│       ├── OFL-CourierPrime.txt
│       ├── OFL-LibreFranklin.txt
│       └── OFL-SourceSerif4.txt
├── robots.txt
├── sitemap.xml
└── README.md
```

## Constraints and accessibility

- Light-only, `color-scheme: light`; no cookies, analytics, `localStorage`, service worker, forms or external requests.
- JavaScript is progressive enhancement only: the copy buttons, copy toast and copy-failure text selection. All content, the FAQ, the disclosures and both mockups work with JS disabled.
- `prefers-reduced-motion: reduce` disables the cursor blink, smooth scrolling and transitions.
- The mockups are HTML/CSS illustrations, labelled as such. They are not screenshots.
- Fonts are self-hosted Libre Franklin, Source Serif 4 and Courier Prime under the SIL Open Font License; each license is included in `assets/fonts/`. Source Serif 4 ships as static 400/600 and italic 400 instances subset to the current site copy for payload (generated with fonttools); regenerate them from the upstream variable font if new copy needs glyphs outside the subset.
- The page keeps a skip link, visible focus rings, semantic figures with text captions, and a keyboard-operable contents menu at narrow widths.

## License

The site content and code are part of the ytmusic-tui repository and use the same MIT license. Libre Franklin, Source Serif 4 and Courier Prime are licensed separately under the SIL Open Font License (see the files in `assets/fonts/`).
