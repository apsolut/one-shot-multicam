# Multicam · visual polish: reference lock + decision ledger

Branch `visual-polish` (from deployed main `ad2a6a8`). It is not merged and not deployed.
Approved direction: the SaaS Ideator spec, locked by Alek.

## Reference lock (Refero)

| Role | Reference | What we take | What we reject |
|---|---|---|---|
| Look base | **Krea** (Refero style "Krea") | The palette: #000 / #171717 / #262626 with white text and #a3a3a3 secondary. No saturated colour. The primary CTA is a white pill with black text. The type steps are 12/14/16/24/36/48. 8px controls. Shadows are almost absent. | Nothing. This is the primary lock. |
| Type + density | **Linear Changelog** (Refero style "Changelog") | Inter. Headings at weight 500–600 with negative tracking (-0.01 to -0.035em). Tabular numbers for times. Pills (9999px) for actions and chips, 8px for cards. Compact rhythm: 24px section gap, 16px padding, 8px element gap. | Its "frosted glass" header. |
| Layout | **Runway lip-sync** https://refero.design/pages/bdf3f0c5-bfbd-466a-aa5a-f4004f3e94ed | Inputs stacked on the left, one large output area on the right. The upload zone is a dark dashed box. Small outline secondary buttons. Credits sit in the top area as one line. | Its blue "Upgrade" pill, which would be a second accent. |
| Progress | **Synthesia generation** https://refero.design/pages/cba3423a-de4c-4203-aefd-c274f9fcd736 | A preview of what is being made, ONE thin progress bar, ONE status line, and nothing else. | — |
| Result | **Frame.io / Flask.do players**: https://refero.design/pages/8d7d27b5, https://refero.design/pages/b56bb090, https://refero.design/pages/26b3ec45 | A large dark framed player. Metadata goes under it as small chips, never burned into the video. | The violet/navy Frame.io palette and its gradients (Refero style 6a82f165). |
| Single-accent precedent | **FLORA** style a986d90b; **Meiro** lime generate button https://refero.design/pages/c0e4ef40 | One green-lime accent on an achromatic UI, used sparingly. | Meiro puts lime on the CTA fill. The locked spec puts white there instead. |
| Upload / assets | Vimeo upload https://refero.design/pages/24a98a84, Runway assets https://refero.design/pages/e011ab97, Runway dashboard https://refero.design/pages/df3bde6d | One calm drop zone with a single icon in a circle, and a short hint line. | Thumbnails grids as decoration. |

## Decision ledger

1. **Typography: Inter Variable, self-hosted** (`web/fonts/InterVariable.woff2`, OFL).
   - Why: Krea's Suisse Intl and Linear both use a neutral grotesk, and Inter is the open substitute.
   - Inter loads with no external request.
   - Weights are 400/500/600 only. No 700–800, which had made the old UI read "shouty".
   - Headings use -0.018em tracking. The hero uses -0.035em.
2. **Type scale: 12 · 13 · 14 · 16 · 20 · 28 · hero clamp(36–60).**
   - Derived from Krea's 12/14/16/24/36/48 and tightened for a dense tool UI (Linear).
   - Body text is 16px, up from 15 / 13.5. The critique said copy was "small and grey", so the size went up and the word count was cut by about half.
3. **Spacing scale: 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64.**
   - Sections are 48 apart, steps 24, elements 8–12. This is the Linear rhythm.
   - **Flattened nesting**:
     - The wizard is no longer a bordered card.
     - The three "how it works" pills are deleted; they duplicated the numbered steps.
     - Steps are hairline-divided rows, not boxes.
     - The consent box is now a plain row.
     - The payment card sits flat inside step 3.
4. **Colour tokens.** Neutral ramp: `--bg #0a0a0a`, `--panel #141414` (cards, one step lighter, 1px `--line #262626`), `--panel2 #1a1a1a`, `--raise #222`, `--line2 #333`, `--line3 #4a4a4a`. Text: `--text #fafafa`, `--muted #a3a3a3`, `--dim #8a8a8a`.
   - **Removed**:
     - the violet/cyan tokens (`#7c5cff`, `#2bd2ff`, `#ff7ad9`)
     - the purple radial body gradient
     - the purple-to-blue gradient headline text
     - the violet CAM-B tags (cameras are now monochrome chips)
5. **One accent: `--acc #b4f25a` (lime).**
   - It is used only for:
     - required-and-empty upload zones and fields (dashed lime border)
     - the focus ring
     - selected states (the chosen partner option and the chosen still, with a lime ring and a check)
   - It is **never a fill on a CTA**.
   - Status colours (`--warn #f5c26b`, `--bad #ff8a8a`) appear only as text or a dot on error and flag states. They are not decoration.
6. **Primary CTA: solid WHITE pill with black text** (the Krea move). This replaces the lime CTA. It is locked by Alek.
   - Why it beats lime: on a monochrome UI the brightest object is white. A white pill is the one obvious action without adding a second hue.
   - Lime then keeps a single meaning: "this needs you / this is selected". Before, lime meant both "required" and "click here", and the meanings blurred.
   - The pill is used for: "Analyze my clip — free", "Generate", "Download MP4", "Retry" and "Resume".
   - The disabled primary is a neutral panel with muted text, not a washed-out lime.
7. **Radius**: 8px controls and inputs; 12px cards, media frames and mode cards; pills (999px) for buttons, chips and the REQUIRED tag. This follows Linear's 8 / 9999 split and Krea's 8 / 14.
8. **Elevation**:
   - No glows and no coloured shadows. The `.primary` lime glow, the running-panel ring, the key-card pulse and the 40px card shadows are gone.
   - Depth comes from one surface step plus a 1px border, as in Krea.
   - Only the modal keeps a single dark drop shadow. The sticky header is opaque, with no frosted glass.
9. **Motion**:
   - Transitions are 140–200ms on border, background and colour, with a 1px press.
   - Cards and panels enter with a 4px rise (320ms, cubic-bezier(.2,.7,.2,1)).
   - Mode-card thumbnails scale 2.5% on hover.
   - Everything is off under `prefers-reduced-motion`.
10. **Icons**:
    - Every emoji in the UI chrome is gone: 🎁 🔑 🎟 📱 👁 🔒 🎭 🌍 ✨ 🔤 ⬇ ↗ ⏳ ⚠ ✅ ↻ 🎥 🖼 🎲 ✕ ▶ ＋.
    - In their place are 2px-stroke SVGs (play, download, camera, eye, close, check mask) or nothing at all.
11. **Mode cards**: real 4:3 stills from each mode's example output, all frames of Alek only.
    - Talk: close-up front.
    - Music: wide shot at the desk.
    - Scene: split frame of the real take and the AI other self.
    - Dub: a 3/4 frame from the dubbed take.
    - They are static JPGs (`th_*.jpg`, about 10–14 KB each). The old 240px looping magenta crops are gone.
12. **Example player**:
    - The examples are now **clean unlabeled exports** (`ex_talk.mp4` from `demo_v6.mp4`, `ex_scene.mp4` from the unlabeled `scene_clone_talk_to_yourself_v4.mp4` (B seated in the same room), 540×960; the gallery Dub item also shows `ex_dub_de.mp4`, the unlabeled German multicam v3). The old ones were 240px loops with burned-in "CAM A real".
    - Camera names are UI chips under the player (A Real take · B AI side · C AI wide).
    - In a Scene or Dub project, the finished video moves into this right-column player (Frame.io), with chips built from the cut list.
13. **Progress panel (Synthesia)**: a preview (the chosen partner still), one 4px white bar, one status line and elapsed/ETA in tabular numbers. Mobile keeps the one-line collapsed bar. Errors use a neutral card with a red dot, the message in a well, and Retry as the white pill.
14. **Free-run banner**: now one quiet left-aligned text line, e.g. "6 of 6 free runs left · one per visitor · or use your own key". It used to be a full-width pill with 🎁.

## Contrast (WCAG AA, computed)

- #fafafa on #0a0a0a: 19.0
- #a3a3a3 on #141414: 7.3
- #8a8a8a on #141414: 5.3, and on #1a1a1a: 5.0
- black on the white CTA: 19.0
- #0b1400 on the lime "Using" tag: 14.2
- warn on panel: 11.2
- error text on panel: 8.1

All normal text is ≥ 4.5:1. Locked steps render at 45% opacity; WCAG exempts inactive components.

## Open questions

- **Colour grade on the examples.** `ex_*` and `th_*` have a mild neutralising grade: saturation 0.62 plus a colour balance that pulls magenta toward neutral. The source room light is hard magenta. This is a display grade of real outputs, not new generation. Drop it if Alek prefers the raw look.
- **Scene thumbnail and player.** These still show the "other self" with a new look (leather jacket, teal city). That is what Scene mode makes, but it can read as a different man. The split thumbnail puts the real take next to it on purpose.
- **Dub example.** `ex_dub.mp4` is the 4-language comparison with its burned-in language names (EN/DE/ES/HR), because no clean single-file version exists in docs/media. Its chips duplicate those names.

## Update 2026-10-28 (approved by Alek)
- **Accent is now Comfy yellow `#f0fe68`** (from comfy.org), replacing lime. Still the only accent. It is used for:
  - the 2px outline and subtle tint on the selected mode card, plus the icon chip on that card
  - outlined REQUIRED pills
  - the dashed required-empty upload zones
  - the consent check
  - focus rings
  - the selected camera tile / partner option / still
  - solid NEW badges with black text (BETA stays a dark pill with a white/30% outline)
- **Contrast:** yellow on #0a0a0a is 18.0:1 and black on yellow is 18.0:1. Yellow is never used as text on white.
- **Generate** stays a white pill with black text.
- **Header:**
  - payment chip (opens a sheet with the 3-way pill switch, key input, Check, Forget key, a one-line privacy note and "How keys are handled")
  - recent-runs clock icon (drawer on desktop, bottom sheet on mobile, dot while a run is unfinished)
  - the same header on the gallery and share pages, with the active item marked and a mobile "Back to Multicam" breadcrumb
- **Pipeline:** hidden until a run starts, then a single collapsed "Details" line under the progress panel.
- **Camera picker:** picture tiles from one demo take (only the angle differs), labelled Strong/Subtle with a one-line hint, plus a framing notice (face size + orientation).
- "Judge code" is shown to users as **"Promo code"**. The backend names and the localStorage key are unchanged.
