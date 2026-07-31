# Prompt Testing

Track image-enhancement prompt iterations for edge cleanup. One **prompt version** = one prompt text + multiple runs on the same fixed inputs.

## How to use

1. Copy the **Prompt template** section for each new prompt version (`Prompt 1`, `Prompt 2`, …).
2. Create an output folder, then run the test script (3× per input by default).
3. Score each run with the rubric below; note failures in **Notes**.
4. Fill the **Summary** row when all runs for that prompt are done.
5. If wall time is **&lt; ~8s**, check whether the file hash matches the input — that usually means **API fallback to original**, not a fast good crop.

### Automated test script

```bash
# 1) Create the folder for this prompt/model session
mkdir -p tests/prompt/prompt_2_gemini_3.1_flash

# 2) Run all inputs × 3 (names files as {runtime}s_{Company}_{1|2|3}.png)
./tests/prompt/run_prompt_test.sh tests/prompt/prompt_2_gemini_3.1_flash

# Optional: only 1 run each
./tests/prompt/run_prompt_test.sh tests/prompt/prompt_2_gemini_3.1_flash 1
```

Defaults: API `http://localhost:8000`, email `test@ebusinesscard.com`, Firebase plist from sibling `e-business-card-app`. Override with `API_BASE`, `EMAIL`, `PASSWORD`, `FIREBASE_PLIST`, or `FIREBASE_API_KEY`.

The script warns when a downloaded image is **byte-identical to the input** (`api-fallback-original`). For Gemini 3.1 that often matches API logs like: `User location is not supported for the API use`.

---

## Fixed inputs

| ID | File | Card type | Known challenge |
|----|------|-----------|-----------------|
| A | `tests/prompt/inputs/Mega.png` | White bilingual card + crest logo | Thin scanner edge / paper shadow; model often **pads black** instead of cropping |
| B | `tests/prompt/inputs/Bloomberg.jpeg` | Dense text, tight margins | Bottom edge remnant; generative **underline artifacts** |
| C | `tests/prompt/inputs/Mine_Wine.png` | White card on cream desk, slight tilt | Low-contrast desk vs card; hardest crop; often left uncropped |

---

## Scoring rubric

Score each run **0–2** per criterion. **Pass** = total ≥ 8/10 and no **0** on edges or aspect ratio.

| Criterion | 0 | 1 | 2 |
|-----------|---|---|---|
| **Edges removed** | Background/desk clearly visible | Thin border or shadow remains | Clean crop to card boundary |
| **Aspect ratio** | Square or clearly wrong shape | Slight stretch/squeeze | ~3:2 landscape preserved |
| **Perspective** | Skew unchanged or worse | Partial straighten | Card looks flat / aligned |
| **Text fidelity** | Text redrawn, missing, or blurred | Minor artifact | Text/logos unchanged |
| **Lighting** | Over-processed or too dark | Acceptable | Mild improvement only |

**Overall:** `pass` | `partial` | `fail`

**Common failure tags:** `edge-left` `edge-right` `desk-visible` `black-pad` `square-output` `aspect-wrong` `text-altered` `underline-artifacts` `timeout` `api-fallback-original`

---

## Prompt comparison

| Session | Avg score (/10) | Pass / partial / fail | Best on | Worst on | Keep? |
|---------|-----------------|------------------------|---------|----------|-------|
| Prompt 1 + Gemini **2.5** Flash Image | **~6.3** | 0 / 6 / 3 | Bloomberg (partial) | Mine_Wine | No — unstable, invents black pad |
| Prompt 1 + Gemini **3.1** Flash Image | **~7.3** (incl. fallbacks) / **~8.5** if exclude 3 fallbacks | 4 / 1 / 4 | Mega_1, Mine_Wine_1/3 when success | Mega_2/3 + Mine_Wine_2 (fallback) | **Conditional** — better when it succeeds; must fix fallback + edge leftovers |
| Prompt 1 + Gemini **3.1** (retry folder) | **~8.6** | 7 / 1 / 1 | Bloomberg | Mega 3, Mine Wine 2 | **Conditional** — still not stable enough |
| Prompt 2 + Gemini **3.1** (pre-Vertex) | mixed | short runs = fallback | when call succeeds | Mega_2/3 fallback | No — location/provider failures |
| Prompt 2 + Gemini **3.1** + **Vertex** | transport **9/9 OK** | crop good; fidelity varies | all inputs crop | underlines / thin edges | **Yes for transport**; keep review UI |
| Prompt 3 + Gemini **3.1** (pixel lock) | mixed | crop OK; **paper warm** on Mega | Mine_Wine crop | Mega beige cast | **No as sole fix** — generative model still recolors |
| Prompt 4 + Gemini **3.1** (negative color constraints) | **~8.0** | 5 / 3 / 1 | Mine_Wine, Bloomberg | Mega bottom edge / underlines | **Better color** than Prompt 3; still not pixel-perfect |

---

## Analysis — why short runs (4s / 5s / 6s) look bad

Verified by SHA-256 hash:

| Output | Wall time | Result |
|--------|-----------|--------|
| `5s_Mega_2.png` | 5s | **Byte-identical to** `inputs/Mega.png` |
| `6s_Mega_3.png` | 6s | **Byte-identical to** `inputs/Mega.png` |
| `4s_Mine_Wine_2.png` | 4s | **Byte-identical to** `inputs/Mine_Wine.png` |

Those are **not** “fast successful enhancements.” They are the app’s silent fallback:

`ImageEnhancementService.enhance_or_original` → on OpenRouter error/timeout → **store original scan**.

Confirmed in API logs for Gemini 3.1 short runs:

`Image enhancement failed: ... "User location is not supported for the API use." ... provider_name: Google AI Studio`

So wall-clock is short because the **image model call failed early** (location/provider block), while OCR still succeeded. Successful Gemini image runs in this set clustered around **~12–26s**. Rule of thumb: **&lt; ~8s + looks like input ⇒ treat as fallback fail**, not a model quality sample.

---

## Analysis — model comparison (same Prompt 1)

### Gemini 2.5 Flash Image (`prompt_1_gemini_2.5_flash/`)

- All 9 files are **real model outputs** (different size/hash from inputs).
- Unstable failure modes:
  - **Black padding** instead of crop (Mega 1 & 3).
  - **Desk still visible** + tilt left (Mine_Wine 1).
  - Bloomberg mostly “almost cropped” but bottom edge / noise / underline artifacts remain.
- Never reached a clean pass on this set.

### Gemini 3.1 Flash Image (`prompt_1_gemini_3.1_flash/`)

- When the call succeeds (~17–26s), quality is **clearly better**:
  - Mega_1: near-clean crop (tiny edge remnant).
  - Mine_Wine_1 / _3: desk mostly gone; much better than 2.5.
  - Bloomberg: tighter crop; still occasional underline artifacts / thin edge.
- **3/9 runs were fallbacks** (4–6s identical originals) → reliability problem, not just prompt quality.
- Aspect ratio sometimes drifts toward squarer frames (~1296×816) even when content is OK.

### What to improve next

1. **Treat fallback as a first-class signal**
   - Log / return whether enhancement applied (`enhanced: true/false`).
   - In prompt testing, **discard** identical-to-input runs from quality averages (or score them as fail with tag `api-fallback-original`).
   - Optionally retry image enhancement 1–2× before falling back.

2. **Keep Gemini 3.1 over 2.5 for this task**, then tighten Prompt 2:
   - Explicit: “Do **not** pad with black/white/any fill. Output frame = card rectangle only.”
   - Explicit: “Do not redraw underlines or OCR boxes.”
   - Explicit: “Preserve every Chinese character exactly.”

3. **Pin aspect ratio in the `/images` payload** (`aspect_ratio: "3:2"`) for Gemini — supported on both models; reduces square-ish drift.

4. **Hard cases (Mine_Wine cream desk)** may still need a hybrid later (CV crop after LLM, or a stronger model like Gemini 3 Pro Image / OpenAI gpt-image). Prompt alone will keep oscillating.

5. **Latency budget**: set expectation that good runs take ~15–25s; short runs need investigation, not celebration.

---

## Prompt 1 — baseline (strong edge removal)

**Goal:** Remove all background and desk edges; preserve card shape and text.

**Implementation source:** `app/services/image_enhancement_service.py:15-27`

**Prompt text:**

```text
Remove ALL background, desk surface, shadows, and outer borders around the business card.
Crop tightly to the card's physical edges so no non-card pixels remain.
Must do:
- Detect the four sides of the card and crop exactly to them.
- Straighten perspective if the card is skewed.
- Keep landscape business-card shape (~3:2 / 85:54). Never output a square.
- Mild lighting fix only so printed text stays readable.
Must not:
- Leave any thin frame, white margin, table edge, or soft fade outside the card.
- Stretch, squeeze, pad, or change aspect ratio.
- Redraw, invent, or alter any text, logo, or color.
- Add filters, borders, or background fill.
Return only the cleaned cropped card image.
```

### Session A — Gemini 2.5 Flash Image

| Setting | Value |
|---------|-------|
| Date | 2026-07-30 |
| Model | `google/gemini-2.5-flash-image` |
| Output dir | `tests/prompt/prompt_1_gemini_2.5_flash/` |
| Enhancement enabled | true |

#### Results

| Input | Run | Output path | Edges | Ratio | Persp. | Text | Light | Total | Overall | Tags / notes |
|-------|-----|-------------|-------|-------|--------|------|-------|-------|---------|--------------|
| A Mega | 1 | `19s_mega_1.png` | 0 | 2 | 2 | 2 | 1 | 7/10 | partial | `black-pad` — invents thick black frame instead of crop |
| A Mega | 2 | `13s_mega_2.png` | 1 | 2 | 1 | 2 | 1 | 7/10 | partial | thin top/right edge; slight skew left |
| A Mega | 3 | `13s_mega_3.png` | 0 | 2 | 2 | 2 | 1 | 7/10 | partial | `black-pad` again — unstable vs run 2 |
| B Bloomberg | 1 | `17s_Bloomberg_1.png` | 1 | 2 | 2 | 1 | 1 | 7/10 | partial | bottom edge remnant; `underline-artifacts` |
| B Bloomberg | 2 | `14s_Bloomberg_2.png` | 1 | 2 | 2 | 1 | 1 | 7/10 | partial | dark perimeter + grain; underlines |
| B Bloomberg | 3 | `16s_Bloomberg_3.png` | 1 | 2 | 2 | 1 | 1 | 7/10 | partial | similar to B1/B2 |
| C Mine_Wine | 1 | `14s_Mine_Wine_1.png` | 0 | 2 | 0 | 2 | 1 | 5/10 | fail | `desk-visible`; tilt unchanged |
| C Mine_Wine | 2 | `17s_Mine_Wine_2.png` | 0 | 2 | 1 | 2 | 1 | 6/10 | fail | background / edge remain |
| C Mine_Wine | 3 | `12s_Mine_Wine_3.png` | 0 | 2 | 1 | 2 | 1 | 6/10 | fail | black/desk surround; not tight crop |

#### Summary

| Metric | Value |
|--------|-------|
| Runs scored | 9/9 |
| Pass / partial / fail | 0 / 6 / 3 |
| Average total | ~6.3/10 |
| Stable on all 3 runs? | **No** — Mega flips black-pad ↔ near-crop |
| Decision | discard as production model for this prompt |
| Next change | switch model → Gemini 3.1 (done below) |

---

### Session B — Gemini 3.1 Flash Image

| Setting | Value |
|---------|-------|
| Date | 2026-07-30 |
| Model | `google/gemini-3.1-flash-image` |
| Output dir | `tests/prompt/prompt_1_gemini_3.1_flash/` |
| Enhancement enabled | true |

#### Results

| Input | Run | Output path | Edges | Ratio | Persp. | Text | Light | Total | Overall | Tags / notes |
|-------|-----|-------------|-------|-------|--------|------|-------|-------|---------|--------------|
| A Mega | 1 | `21s_Mega_1.png` | 1 | 2 | 2 | 2 | 2 | 9/10 | pass | best Mega; tiny top/bottom edge remnant |
| A Mega | 2 | `5s_Mega_2.png` | 0 | 2 | 0 | 2 | 1 | 5/10 | fail | **`api-fallback-original`** — hash = input Mega.png |
| A Mega | 3 | `6s_Mega_3.png` | 0 | 2 | 0 | 2 | 1 | 5/10 | fail | **`api-fallback-original`** — identical to Mega_2 / input |
| B Bloomberg | 1 | `26s_Bloomberg_1.png` | 2 | 2 | 2 | 1 | 1 | 8/10 | pass | tight crop; `underline-artifacts` |
| B Bloomberg | 2 | `17s_Bloomberg_2.png` | 1 | 2 | 2 | 2 | 2 | 9/10 | pass | cleanest Bloomberg; micro edge only |
| B Bloomberg | 3 | `22s_Bloomberg_3.png` | 1 | 2 | 2 | 1 | 1 | 7/10 | partial | thin bottom/right edge + underlines |
| C Mine_Wine | 1 | `20s_Mine_Wine_1.png` | 1 | 2 | 2 | 1 | 2 | 8/10 | pass | desk mostly gone; check Chinese glyph fidelity |
| C Mine_Wine | 2 | `4s_Mine_Wine_2.png` | 0 | 2 | 0 | 2 | 1 | 5/10 | fail | **`api-fallback-original`** — hash = input Mine_Wine.png |
| C Mine_Wine | 3 | `20s_Mine_Wine_3.png` | 1 | 2 | 2 | 2 | 2 | 9/10 | pass | thin dark perimeter remains |

#### Summary

| Metric | Value |
|--------|-------|
| Runs scored | 9/9 |
| Pass / partial / fail | 4 / 1 / 4 |
| Average total | ~7.3/10 (all) · **~8.5/10** (6 successful generations only) |
| Stable on all 3 runs? | **No** — 3/9 silent fallbacks; successful runs much better than 2.5 |
| Decision | keep as candidate model; fix reliability + Prompt 2 |
| Next change | (1) surface enhancement success flag / retry on fail (2) Prompt 2 ban black-pad + underlines (3) set `aspect_ratio: "3:2"` (4) optional Gemini 3 Pro / OpenAI if Mine_Wine still flaky |

---

## Prompt 2 — geometric crop (content-preserving)

**Goal:** Stronger crop + perspective correction; treat card interior as immutable; avoid redrawing text/underlines. First prompt used with Gemini 3.1 after Prompt 1.

**Implementation source (historical):** `app/services/image_enhancement_service.py` when `tests/prompt/prompt_2_*` was produced.

**Prompt text:**

```text
Extract the physical business card using geometric cropping and perspective correction only.

CROP:
- Detect the four physical card edges.
- Crop edge-to-edge so the card touches all four output edges.
- Remove every pixel outside the card, including desk, table, fingers, shadows, and surroundings.
- Do not add padding, margins, borders, or background fill.

GEOMETRY:
- Correct tilt and keystone distortion so the card appears flat and top-down.
- Make opposite card edges parallel.
- Preserve the card’s physical corners; do not redraw or reshape them.
- Preserve the original landscape orientation and physical business-card proportions.

CONTENT PRESERVATION — HIGHEST PRIORITY:
- Treat everything inside the card boundary as immutable.
- Preserve every character, digit, logo, underline, color, and design element exactly.
- Do not redraw, retype, reconstruct, sharpen, replace, or reinterpret content.
- Do not invent missing details.
- If text is blurry, leave it blurry.
- Do not add underlines or other marks.

LIGHTING:
- Apply only mild global lighting normalization if necessary.
- Do not change ink, paper, logo, or background colors.

Return only the cropped and perspective-corrected card image.
Never output a square image.
```

**Hypothesis:** Explicit geometry + “immutable interior” reduces black-pad / redraw; still allows mild lighting.

### Session A — Gemini 3.1 Flash (before Vertex pin)

| Setting | Value |
|---------|-------|
| Date | 2026-07-30 |
| Model | `google/gemini-3.1-flash-image` |
| Output dir | `tests/prompt/prompt_2_gemini_3.1_flash/` |
| Enhancement enabled | true |

#### Results (spot-check)

| Input | Run | Output path | Overall | Tags / notes |
|-------|-----|-------------|---------|--------------|
| A Mega | 1 | `19s_Mega_1.png` | partial/pass | real generation |
| A Mega | 2 | `9s_Mega_2.png` | fail | **`api-fallback-original`** (short runtime) |
| A Mega | 3 | `10s_Mega_3.png` | fail | **`api-fallback-original`** (short runtime) |
| B Bloomberg | 1–3 | `21s` / `20s` / `16s` | mixed | successful runs still vary on underlines / thin edges |
| C Mine_Wine | 1–3 | `20s` / `22s` / `16s` | mixed | crop improved vs Prompt 1 2.5; fidelity still unstable |

#### Summary

| Metric | Value |
|--------|-------|
| Transport | Still hit AI Studio location failures → silent original fallback on short runs |
| Decision | Keep Gemini 3.1; pin Vertex Global + reviewable candidates |
| Next change | Prompt 2 + Vertex provider pin (Session B) |

### Session B — Vertex Global + review candidates

| Setting | Value |
|---------|-------|
| Date | 2026-07-30 |
| Model | `google/gemini-3.1-flash-image` |
| Provider | `google-vertex/global` only; fallback disabled |
| Output dir | `tests/prompt/prompt_2_vertex_review_retry/` |
| Enhancement enabled | true |

#### Results

| Metric | Value |
|--------|-------|
| Runs | **9/9 AI candidates** (no AI Studio location failures, no original-image fallbacks) |
| Runtime | 14–17 seconds |
| Edges | Surroundings removed; tight crop. Minor perimeter / straight-bottom-edge leftovers remain |
| Fidelity | Underlines and some character rendering still vary; paper tone can warm slightly |
| Decision | Keep Gemini 3.1 for cost/latency; **do not auto-replace original** — Confirm / Retry / Use original |
| Next change | Prompt 3 — ban color shift / force “pixel lock” wording |

---

## Prompt 3 — mathematical warping / pixel lock

**Goal:** Stop white→beige/yellow paper shift and text color drift while keeping edge crop. Frame the model as a geometry-only warper, not a photo enhancer.

**Implementation source (historical):** earlier `ENHANCEMENT_PROMPT` before Prompt 4.

**Prompt text:**

```text
System Role: You are a strict mathematical image warping engine. Perform geometric homography and perspective correction only. Do not re-render pixels.

EXECUTION INSTRUCTIONS:
- Detect the 4 outermost physical boundaries of the card material.
- Crop edge-to-edge. The cropped physical card must touch all 4 output boundaries perfectly.
- Delete all pixels outside the card boundary (including background tables, shadows, or scanner artifacts).
- Mathematically warp the perspective to eliminate tilt and keystone distortion. The final output must be flat, top-down, and rectangular.
- Maintain original landscape proportions. Do not output a square.

PIXEL LOCK RULES (CRITICAL FOR CALCULATION):
- The original RGB channel matrix inside the card boundary must remain entirely unchanged.
- Do not apply any post-processing, color correction, denoising, contrast enhancements, or exposure adjustments.
- The background color must remain a 1:1 exact pixel match to the input image. (If the original background is white, it must stay white. Do not shift color balance or introduce warm/beige tints).
- Do not reconstruct, sharpen, or redraw text, logos, or markings.

Return exclusively the cropped and perspective-warped card image. Do not output any text response.
```

**Hypothesis:** Stronger “do not re-render / RGB lock” language reduces warm paper cast; generative models may still ignore it.

### Session A — Gemini 3.1 Flash Image

| Setting | Value |
|---------|-------|
| Date | 2026-07-30 |
| Model | `google/gemini-3.1-flash-image` |
| Provider | `google-vertex/global` (expected) |
| Output dir | `tests/prompt/prompt_3_gemini_3.1_flash/` |
| Enhancement enabled | true |

#### Results

| Input | Run | Output path | Edges | Ratio | Persp. | Text | Light | Total | Overall | Tags / notes |
|-------|-----|-------------|-------|-------|--------|------|-------|-------|---------|--------------|
| A Mega | 1 | `20s_Mega_1.png` | 1 | 2 | 2 | 1 | 0 | 6/10 | fail | crop OK-ish; **`paper-warmed`** cream/beige vs input white; bottom edge remnant; underlines |
| A Mega | 2 | `18s_Mega_2.png` | | 2 | | | | /10 | | inspect for color shift |
| A Mega | 3 | `23s_Mega_3.png` | | 2 | | | | /10 | | inspect for color shift |
| B Bloomberg | 1 | `25s_Bloomberg_1.png` | | 2 | | | | /10 | | |
| B Bloomberg | 2 | `17s_Bloomberg_2.png` | | 2 | | | | /10 | | |
| B Bloomberg | 3 | `14s_Bloomberg_3.png` | | 2 | | | | /10 | | |
| C Mine_Wine | 1 | `24s_Mine_Wine_1.png` | | 2 | | | | /10 | | |
| C Mine_Wine | 2 | `18s_Mine_Wine_2.png` | 1 | 2 | 2 | 2 | 1 | 8/10 | pass | desk gone; thin perimeter remains; paper closer to neutral |
| C Mine_Wine | 3 | `18s_Mine_Wine_3.png` | 1 | 2 | 2 | 2 | 1 | 8/10 | pass | same; straight bottom edge still treated as border |

#### Summary

| Metric | Value |
|--------|-------|
| Runs scored | 3/9 spot-checked (+ folder has full 9 files) |
| Pass / partial / fail | mixed — crop often OK; **color lock unreliable** |
| Stable on all 3 runs? | **No** — Mega still warms paper; bottom straight edge sometimes kept |
| Decision | Prompt wording alone cannot guarantee RGB fidelity on a generative image model |
| Next change | Prompt 4 — add explicit negative color constraints (`#FFFFFF`, ban beige/cream) |

---

## Prompt 4 — geometric crop + negative color constraints

**Goal:** Keep Prompt 3’s geometry focus, but add an explicit negative list against warm paper casts (`beige`, `cream`, `yellow tint`) and force white backgrounds toward pure `#FFFFFF`.

**Implementation source:** current `app/services/image_enhancement_service.py` `ENHANCEMENT_PROMPT`

**Prompt text:**

```text
Task: Complete strict geometric image cropping and perspective translation on the provided image. Do not generate or paint new artistic textures.

GEOMETRIC BOUNDARIES:
- Detect the 4 outermost physical edges of the card material.
- Crop edge-to-edge so the card touches all four output borders.
- Delete 100% of pixels outside the card boundary (remove tables, scanner edges, hands, shadows).
- Correct all tilt and keystone distortion to force a flat, top-down rectangular plane.
- Maintain original horizontal landscape proportions. Never output a square image.

PIXEL INVARIANCE (COLOR PROTECTION MATRIX):
- Treat the RGB pixel values inside the card as mathematical constants.
- Do not add lighting filters, contrast changes, denoising, or style enhancements.
- The output background color must map 1:1 identically to the input image background. If the input background is white, the output must remain pure #FFFFFF white. Do not warm the tint or add cream, off-white, or beige hues.
- Do not reconstruct, sharpen, or repaint logos, lines, or characters.

CRITICAL NEGATIVE CONSTRAINTS (DO NOT INCLUDE IN OUTPUT):
[NEGATIVE_PROMPT: beige, cream, off-white, warm tones, studio lighting, yellow tint, gradient background, paper texture, ambient occlusion shadows, background bleeding]
```

**Hypothesis:** Explicit `#FFFFFF` + negative warm-tone ban reduces white→beige drift more than Prompt 3’s softer “stay white” wording.

### Session A — Gemini 3.1 Flash Image

| Setting | Value |
|---------|-------|
| Date | 2026-07-31 |
| Model | `google/gemini-3.1-flash-image` |
| Provider | `google-vertex/global` (expected) |
| Output dir | `tests/prompt/prompt_4_gemini_3.1_flash/` |
| Enhancement enabled | true |

#### Results

| Input | Run | Output path | Edges | Ratio | Persp. | Text | Light | Total | Overall | Tags / notes |
|-------|-----|-------------|-------|-------|--------|------|-------|-------|---------|--------------|
| A Mega | 1 | `21s_Mega_1.png` | 2 | 2 | 2 | 1 | 2 | 9/10 | pass | white paper much closer to input; underlines still appear |
| A Mega | 2 | `21s_Mega_2.png` | 1 | 2 | 2 | 1 | 2 | 8/10 | pass | thin bottom scan edge left; underlines |
| A Mega | 3 | `22s_Mega_3.png` | 2 | 2 | 2 | 1 | 2 | 9/10 | pass | clean white; design gold divider kept; underlines |
| B Bloomberg | 1 | `15s_Bloomberg_1.png` | 1 | 2 | 2 | 2 | 2 | 9/10 | pass | tight crop; faint bottom edge remnant |
| B Bloomberg | 2 | `14s_Bloomberg_2.png` | 1 | 2 | 2 | 1 | 1 | 7/10 | partial | paper slightly warm/off-white; `underline-artifacts` |
| B Bloomberg | 3 | `17s_Bloomberg_3.png` | 1 | 2 | 2 | 2 | 2 | 9/10 | pass | neutral paper; thin bottom edge |
| C Mine_Wine | 1 | `19s_Mine_Wine_1.png` | 2 | 2 | 2 | 2 | 1 | 9/10 | pass | desk gone; red bars preserved; mild off-white grain |
| C Mine_Wine | 2 | `18s_Mine_Wine_2.png` | 2 | 2 | 2 | 2 | 1 | 9/10 | pass | clean crop; underlines on contact lines |
| C Mine_Wine | 3 | `19s_Mine_Wine_3.png` | 1 | 2 | 2 | 2 | 1 | 8/10 | pass | thin perimeter / off-white grain remains |

#### Summary

| Metric | Value |
|--------|-------|
| Runs scored | 9/9 |
| Pass / partial / fail | 7 / 2 / 0 |
| Average total | **~8.6/10** |
| Stable on all 3 runs? | **Mostly** — color better than Prompt 3; Mega no longer strongly beige |
| Decision | Best prompt so far for white preservation + crop; still generative (underlines / thin edges vary) |
| Next change | Keep review UI; if pixel-perfect white/text required → hybrid LLM corners + OpenCV warp |

---

## Prompt template

Copy this block for each prompt version.

### Prompt N — `<short name>`

**Goal:** *(what this prompt tries to fix)*

**Prompt text:**

```text
(paste ENHANCEMENT_PROMPT here)
```

**Hypothesis:** *(1 line)*

#### Results

| Input | Run | Output path | Edges | Ratio | Persp. | Text | Light | Total | Overall | Tags / notes |
|-------|-----|-------------|-------|-------|--------|------|-------|-------|---------|--------------|
| A Mega | 1 | | | | | | | /10 | | |
| A Mega | 2 | | | | | | | /10 | | |
| A Mega | 3 | | | | | | | /10 | | |
| B Bloomberg | 1 | | | | | | | /10 | | |
| B Bloomberg | 2 | | | | | | | /10 | | |
| B Bloomberg | 3 | | | | | | | /10 | | |
| C Mine_Wine | 1 | | | | | | | /10 | | |
| C Mine_Wine | 2 | | | | | | | /10 | | |
| C Mine_Wine | 3 | | | | | | | /10 | | |

#### Summary

| Metric | Value |
|--------|-------|
| Runs scored | /9 |
| Pass / partial / fail | / / |
| Average total | /10 |
| Stable on all 3 runs? | |
| Decision | |
| Next change | |
