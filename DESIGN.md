---
name: ytmusic-tui
description: A terminal-first YouTube Music client documented as a printed field manual.
colors:
  paper: "#f1eee5"
  paper-shade: "#e9e5d9"
  ink: "#191813"
  ink-muted: "#4d4a41"
  ink-faint: "#696459"
  printer-blue: "#1d4c8c"
  oxblood: "#9b3428"
  rule: "#19181338"
  terminal-black: "#12110f"
  terminal-raised: "#1a1916"
  terminal-ink: "#e8e3d7"
  terminal-muted: "#9b978b"
  terminal-rule: "#34312b"
  status-ok: "#9acb86"
  status-amber: "#e0b36b"
  status-bad: "#d47a6c"
typography:
  display:
    fontFamily: "Libre Franklin, Franklin Gothic Medium, Arial Narrow, Arial, sans-serif"
    fontSize: "clamp(2.65rem, 6.4vw, 5.3rem)"
    fontWeight: 650
    lineHeight: 0.95
    letterSpacing: "-0.035em"
    wordSpacing: "0.09em"
  headline:
    fontFamily: "Libre Franklin, Franklin Gothic Medium, Arial Narrow, Arial, sans-serif"
    fontSize: "clamp(1.35rem, 2.3vw, 1.8rem)"
    fontWeight: 550
    lineHeight: 1.35
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Libre Franklin, Franklin Gothic Medium, Arial Narrow, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  deck:
    fontFamily: "Source Serif 4, Georgia, Times New Roman, serif"
    fontSize: "clamp(1.08rem, 1.45vw, 1.3rem)"
    fontWeight: 400
    lineHeight: 1.55
  body:
    fontFamily: "Source Serif 4, Georgia, Times New Roman, serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.62
  label:
    fontFamily: "Courier Prime, Courier New, Courier, monospace"
    fontSize: "11px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0.15em"
  button:
    fontFamily: "Courier Prime, Courier New, Courier, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "0.08em"
  mono:
    fontFamily: "Courier Prime, Courier New, Courier, monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.6
  terminal:
    fontFamily: "Courier Prime, Courier New, Courier, monospace"
    fontSize: "clamp(8.5px, 1.05vw, 12.5px)"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "tnum"
rounded:
  square: "0"
  keycap: "2px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "32px"
  xl: "64px"
  page-gutter: "40px"
  section: "clamp(56px, 7.5vw, 104px)"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.button}"
    rounded: "{rounded.square}"
    padding: "10px 13px"
  button-primary-hover:
    backgroundColor: "{colors.printer-blue}"
    textColor: "{colors.paper}"
  button-copy:
    backgroundColor: "transparent"
    textColor: "{colors.printer-blue}"
    typography: "{typography.button}"
    rounded: "{rounded.square}"
    padding: "6px 8px"
  button-copy-filled:
    backgroundColor: "{colors.printer-blue}"
    textColor: "{colors.paper}"
    typography: "{typography.button}"
    rounded: "{rounded.square}"
    padding: "7px 10px"
  button-copy-filled-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  command-plate:
    backgroundColor: "{colors.paper-shade}"
    textColor: "{colors.ink}"
    typography: "{typography.mono}"
    rounded: "{rounded.square}"
    padding: "12px 12px 12px 14px"
  code-plate:
    backgroundColor: "{colors.paper-shade}"
    textColor: "{colors.ink}"
    typography: "{typography.mono}"
    rounded: "{rounded.square}"
    padding: "10px 12px"
  terminal-plate:
    backgroundColor: "{colors.terminal-black}"
    textColor: "{colors.terminal-ink}"
    typography: "{typography.terminal}"
    rounded: "{rounded.square}"
    padding: "0"
  status-stamp:
    backgroundColor: "transparent"
    textColor: "{colors.oxblood}"
    typography: "{typography.label}"
    rounded: "{rounded.square}"
    padding: "4px 8px"
---

# Design System: ytmusic-tui

## Overview

**Creative North Star: "The Printed Field Manual"**

The site is a product manual set on paper, not a SaaS landing page. It borrows the physical grammar of a technical booklet: a running head, a contents row, hairline rules, a narrow margin column for section labels, hanging notes, a stamp, and one dark terminal plate reproduced as a printed specimen. The page is light-only; the terminal illustration is the single dark object on the paper.

The voice is plain, dry and specific. The page states what the client does, what it requires and where it stops. It never pads the record with invented proof. The typography carries the personality: Franklin's tight grotesque for the title, a bookish Source Serif for reading, and Courier Prime for anything that belongs to a command line.

The result should feel like a well-made manual someone kept on a desk: legible, opinionated, slightly inky, and easy to use. It should never feel like a themed component library.

**Key Characteristics:**
- Uncoated paper, black ink, one printer's blue and one oxblood accent.
- A three-family type system: grotesque display, bookish serif body, typewriter labels and commands.
- Rules and margin notes do the work that cards and shadows do elsewhere.
- A single terminal plate is the visual proof-of-surface; it is labelled an illustration, not a screenshot.
- Static, light, fast: no external requests, cookies, analytics or build step.

## Colors

A paper-and-ink palette with a printer's blue for marks and an oxblood red for limits. The terminal plate introduces a quarantined dark palette that never leaks into the page chrome.

### Primary
- **Printer's Blue** (#1d4c8c): links, section labels, key-group headings, copy controls, focus rings, selection and the registration corners of the terminal plate. It is a mark, never a large fill.
- **Paper** (#f1eee5): the page ground. **Paper Shade** (#e9e5d9) is used for command plates, code plates and the colophon.

### Secondary
- **Oxblood** (#9b3428): the stamp border and text, and the small rule beside each caveat. It appears only where the manual marks a limit.

### Neutral
- **Ink** (#191813): body and display text.
- **Ink Muted** (#4d4a41): secondary prose and notes.
- **Ink Faint** (#696459): section captions, metadata and fine print. The lowest measured pair, Ink Faint on Paper, is 5.07:1.
- **Rule** (`#19181338`, Ink at 22%): hairline dividers. Emphasis rules use Ink at full strength.

### Terminal
- **Terminal Black** (#12110f) and **Terminal Raised** (#1a1916): the plate ground.
- **Terminal Ink** (#e8e3d7), **Terminal Muted** (#9b978b) and **Terminal Rule** (#34312b): terminal text, secondary text and dividers.
- **Status Ok** (#9acb86), **Status Amber** (#e0b36b) and **Status Bad** (#d47a6c): progress, selection and failure inside the terminal illustration only.

### Named Rules
**The Blue Mark Rule.** Blue identifies a control, a link or a label. It never becomes a section background or a decorative gradient.

**The Oxblood Limit Rule.** Red is reserved for the pre-1.0 stamp and the caveats. It is not an alternate call-to-action color.

**The Quarantined Terminal Rule.** The dark palette and its status colors live only inside the terminal plate. The page itself remains paper.

## Typography

**Display Font:** Libre Franklin (with Franklin Gothic Medium, Arial Narrow, Arial, sans-serif)
**Body Font:** Source Serif 4 (with Georgia, Times New Roman, serif)
**Label/Mono Font:** Courier Prime (with Courier New, Courier, monospace)

**Character:** A tight American grotesque for the headline, a bookish serif for reading, and a typewriter face for anything that belongs in a terminal. The pairing is deliberately institutional: a manual, not a magazine.

Source Serif 4 is self-hosted as static 400 normal, 600 normal and 400 italic instances; the optical-size axis is baked at 18 for body, 14 for the small 600 and 15 for italic. This keeps the font payload around 86 KB while preserving the family and weights. It is an implementation detail, not permission to substitute another serif.

### Hierarchy
- **Display** (650, `clamp(2.65rem, 6.4vw, 5.3rem)`, 0.95, -0.035em, word-spacing 0.09em): the hero title only. Maximum 14ch; balance the lines.
- **Headline** (550, `clamp(1.35rem, 2.3vw, 1.8rem)`, 1.35, -0.02em): Libre Franklin section leads. Maximum 40ch.
- **Title** (650, 17px, 1.3, -0.01em): principle headings, caveat titles and small display emphasis.
- **Deck** (400, `clamp(1.08rem, 1.45vw, 1.3rem)`, 1.55): the Source Serif hero deck; maximum 47ch.
- **Body** (400, 18px desktop / 17px mobile, 1.62): reading prose; maximum 68ch.
- **Note** (400, 13–15.5px, 1.5–1.6): Source Serif margin notes, figure notes and caveat copy in Ink Muted or Ink Faint.
- **Label** (700, 11px, 1.4, 0.15em, uppercase): Courier Prime section heads, figure labels and stamps.
- **Button** (400, 11px, 1.2, 0.08em, uppercase): Courier Prime primary and copy controls.
- **Mono** (400, 12.5px, 1.6): command lines and code. **Terminal** (400, `clamp(8.5px, 1.05vw, 12.5px)`, 1.5, tabular numbers): the plate only.

### Named Rules
**The One Display Voice Rule.** Libre Franklin is the only display face. Do not introduce a second sans, a script or a decorative serif for headings.

**The Typewritten Label Rule.** Small caps-like labels are Courier Prime, uppercase, tracked and never set in the serif.

**The Margin Italic Rule.** Italic is a margin voice: the hero margin note and the occasional `em` inside a figure caption. It is not a body style.

## Layout

The page is a 1280px manual with a 40px gutter (32px below 1120px, 24px below 760px, 20px at the narrow end) and a 68ch reading measure. Sections sit on a two-column grid: a 180px margin column for the label and its note, then the body. The gap is `clamp(32px, 5vw, 64px)`; sections are separated by `clamp(56px, 7.5vw, 104px)` of vertical space and a 1px Ink rule.

The running head is a three-part line: wordmark, section context and version/date/repository metadata. The contents row sits beneath it. The hero pairs a 1fr title block with a 240px margin note, then a 1fr / 0.86fr lower grid: deck and proof line on the left, synopsis and command plate on the right. The first viewport must show the headline, the deck, the synopsis command, the Copy control and the single black "Read the installation" action; the terminal plate may begin below.

Responsive behavior is part of the grammar, not a separate layout:
- At 1120px the gutter tightens and the margin column narrows to 156px.
- At 940px the header switches to the "Contents" disclosure, the running date is hidden and the hero margin note is removed.
- At 760px the page body drops to 17px, the hero lower grid stacks, the repository link is hidden and the two-column sections become one column.
- At 600px and 390px the plates, key lists and controls collapse without horizontal scrolling.

The page has been checked from 320px to 1920px with no horizontal overflow. Preserve the 68ch measure and the 40ch section-lead cap; do not let display type run wider than its measure.

Print is a first-class target: A4, white paper, hidden chrome and controls, one-column sections, a two-column principles/key layout, and a three-column colophon that starts on its own page. The terminal specimen prints in black and white.

### Named Rules
**The First-Viewport Rule.** The headline, deck, synopsis command, Copy control and the single primary installation action must be visible before the terminal plate. Do not push the install path below the fold.

## Elevation & Depth

The page is flat by doctrine. Depth comes from paper tone, hairline rules, the 4.5% fixed grain overlay and the printed terminal specimen, not from cards or glows. The terminal plate has a 9px frame, top and bottom rules, and 14px blue registration corners; it reads as an inserted printed plate.

The only functional shadow is the mobile contents dropdown (`0 14px 28px #19181324`). It exists to separate the overlay from the page, not to decorate it. Do not add card shadows, glass, blur, gradient text or faux-3D layers.

### Named Rules
**The Flat-By-Default Rule.** Surfaces are flat at rest. The mobile contents dropdown is the only shadow in the system.

## Shapes

The system is square. Buttons, command plates, code blocks, terminal plates and figures have 0 radius. The only 2px radius is on keycaps, which also carry a 2px bottom border to read as a physical key. Rules are 1px. The stamp is a 1px oxblood rectangle rotated -1.5 degrees. There are no pills, rounded cards or floating icon tiles.

Focus is a 2px Printer's Blue outline with 3px offset. The skip link becomes visible on focus. Selection is Printer's Blue with Paper text.

### Named Rules
**The Square-Corner Rule.** Buttons, plates and figures are square. The only radius in the system is the 2px keycap.

## Components

**Wordmark.** The visible `ytmusic-tui(1)` label is the accessible name of the home link. A visually hidden `home` qualifies the destination without replacing the visible label, so the accessible name still contains the visible text (WCAG 2.5.3, Label in Name).

**Primary button.** Ink fill, Paper text, Courier Prime 11px uppercase, 10px × 13px padding, 1px Ink border, 18px top margin. Hover becomes Printer's Blue. There is one primary action per view.

**Copy control.** A ghost Printer's Blue control at 6px × 8px outside the command plate; inside the synopsis it is filled blue at 7px × 10px. Hover inks it. Success changes the label to "COPIED" and shows the fixed ink toast; failure keeps the command selected and shows "Copy failed. The command is selected; copy it manually." The control is hidden without JavaScript, and the toast is an `aria-live="polite"` status.

**Command plate.** Paper Shade ground, 1px Ink border, a three-column grid of blue prompt, command and Copy control. The install URL and repository name stay unbroken and the line breaks after `github.com/`; the prompt is not selectable.

**Code blocks.** Paper Shade ground, 1px Ink border, a small head with a mono label and a Copy control, and a 12px mono pre with horizontal scrolling for long commands.

**Terminal plate.** The plate frame is the only dark object. Inside, Courier Prime is set at `clamp(8.5px, 1.05vw, 12.5px)` with tabular numbers. Rows use a three-column grid; the selected row inverts to Terminal Ink ground with Terminal Black text. Status colors appear only in the plate. The figure caption names it "Illustration, not a screenshot."

**FAQ.** Native `details`/`summary` rows; 18px Libre Franklin 550 question, blue `+` / `−` marker, 16px Source Serif answer in Ink Muted. The first item is open. All answers remain readable with JavaScript disabled.

**Mobile contents.** A `details` disclosure with a "Contents" summary, a 240px paper dropdown, 1px Ink border, mono 12px links and the functional shadow. Choosing a destination closes it; outside click closes it; Escape closes it and returns focus to the summary.

**Stamp.** A 1px oxblood border, 11px Courier Prime, 0.16em tracking, -1.5deg rotation, visible text "Pre-1.0". The text is the accessible name; do not put an `aria-label` on the generic span.

**Keycaps.** 1px Ink border with a 2px bottom border, 2px radius, Paper ground, mono at 0.78em. Key names and letters are text, not color-only icons.

## Weight budget

The site ships pre-built and self-contained. These are the measured raw file sizes of the shipped page.

| Asset | Budget | Shipped |
| --- | --- | --- |
| HTML | 40 KB | 39,866 B |
| CSS | 30 KB (revised) | 29,680 B |
| JavaScript | 8 KB | 3,858 B |
| Fonts | 120 KiB | 86,520 B |
| Page (initial load) | 350 KB | 159,924 B |

The CSS ceiling was revised from 25 KB to 30 KB after the final audit. The served stylesheet is 29,680 B minified (6,931 B gzip), within the revised ceiling. The original 25 KB target was only reachable by obfuscating all class names and custom properties and stripping redundant `@font-face` syntax; that variant measured 24,788 B, but it would make the HTML, CSS and JavaScript unreadable, so the readable stylesheet and the print/responsive layers were kept. A coverage audit finds 93% of it used on screen; the remainder is the print block and roughly 600 B of reduced-motion, focus and WebKit-only selectors that a screen audit cannot observe. HTML, JavaScript, fonts and total page weight are within budget.

## Do's and Don'ts

**Do**
- Keep the printed-manual grammar: rules, margin labels, hanging notes, paper tones and the one dark specimen.
- Keep the first-viewport promise: headline, deck, synopsis command, Copy and the single primary installation action.
- Use the token values in this file; the CSS custom properties in `site/assets/styles.src.css` are the source of truth.
- Keep the site static and self-contained: no build step, no external requests, no cookies, analytics, `localStorage` or service worker.
- Write English site copy. Portuguese belongs to the app strings inside the terminal and interface illustrations.
- Keep every product claim verifiable in `README.md`, `pyproject.toml` or the release notes. No invented user counts, testimonials, benchmarks, press, pricing, PyPI listing or platform support.
- Keep all text at WCAG AA or better; the lowest measured shipping pair is 5.07:1.
- Label the HTML/CSS mockups as illustrations, not screenshots.
- Preserve the focus ring, skip link, reduced-motion behavior and no-JS content.

**Don't**
- Don't add cards, rounded panels, glows, gradients, glass, icon tiles, metrics bands, testimonial carousels or a second dark mode.
- Don't use Printer's Blue as a large background wash or Oxblood as a second call-to-action.
- Don't use the terminal amber or status colors outside the terminal plate.
- Don't add shadows other than the mobile contents dropdown.
- Don't introduce another typeface or swap the body serif; don't set body copy in Libre Franklin.
- Don't add an `aria-label` to a generic `span`; use visible text or a valid role.
- Don't move, straighten or restyle the stamp; it is a deliberate imperfection.
- Don't let the page become a generic SaaS landing page. The manual is the product surface.
