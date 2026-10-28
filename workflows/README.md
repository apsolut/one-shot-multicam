# ComfyUI workflows

These are the exact ComfyUI graphs the app submits to Comfy Cloud, in **API format** (`*_api.json`: what
`POST /prompt` takes). Each one has a `*_bindings.json` next to it that maps the app's inputs to node ids
(`"seed": [["24", "noise_seed"], ["50", "noise_seed"]]` means "write the seed into node 24 and node 50").
Every other input keeps the value in the file.

**Two files per workflow.** Drag `*_ui.json` into ComfyUI to view and edit the node graph. `*_api.json` is what the
app submits.

| Graph (drag into ComfyUI) | What you see |
|---|---|
| [`crossview_ui.json`](crossview_ui.json) | The CrossView re-shoot, annotated: reference video and real audio, models, camera prompt, stage 1, stage 2 (×2 upscale), decode |
| [`idlora_ui.json`](idlora_ui.json) | The official Comfy Cloud "LTX-2.3: ID LoRA" template (the sampling graph is a subgraph) |
| [`qwen_angles_ui.json`](qwen_angles_ui.json) | The official Comfy Cloud "Qwen 1 Click Multiple Character Angles" template (8 angle subgraphs) |
| [`scene_edit_ui.json`](scene_edit_ui.json) | The Scene partner-stills graph flattened: input images on top, one group per edit branch (`p1..p8` → `e1..e8`), SaveImage outputs |
| [`dub_ui.json`](dub_ui.json) | The LipDub/DubIt graph in stage groups: inputs → models + LoRAs → prompt + conditioning → sampling (2 stages) → decode + output |
| [`translate_ui.json`](translate_ui.json) | Gemma 3 12B `TextGenerate` → Preview Any |

In every graph, the nodes the app sets on each job are yellow and their titles say `app sets: …`. Each node also
keeps its API id in Properties → `api_id`, and a notes node at the top explains the graph. The UI files were built
and checked with the official ComfyUI frontend (1.58.1), loaded with Comfy Cloud's node definitions:

- The frontend converts each `*_api.json` itself, so the widget order comes from the real node definitions.
- Each `*_ui.json` was loaded back and converted to a prompt, and it matched its `*_api.json` input for input and
  link for link. The only additions are optional inputs the frontend fills with their defaults:
  - `CreateVideo`: `bit_depth=auto`, `color_space=sRGB`, `codec=none`
  - `LTXAddVideoICLoRAGuide`: `use_streaming=false`, `tile_frames=97`
  - `SaveVideo`: `format.codec=auto`
  - `TextGenerate`: `mtp=auto`
- `tests/test_workflows_ui.py` repeats the check offline. `tools/workflow_ui/` rebuilds and re-checks the files.

Seeds in the generated graphs are set to *fixed*, so queuing one as-is reproduces the submitted values. If you
open the graph in ComfyUI and export it with *Export (API)*, node ids become plain integers. Keep using the
committed `*_api.json` and its bindings with the app.

**Everything here is open weights.** There are no partner or closed API nodes. You can run every graph on your
own GPU in ComfyUI, or on Comfy Cloud.

The files contain no keys, user file names or local paths. Image, audio and video inputs hold template
placeholders (`ref.mp4`, `character_input.png`, `vintage_thinker.png`, `ltx23_reference_audio.mp3`), which the app
replaces with the uploaded file for each job. `tests/test_workflows.py` checks this, and it also checks that
every binding points at a real node input.

| File | Used by | What it does | Cost on Comfy Cloud |
|---|---|---|---|
| [`crossview_api.json`](crossview_api.json) | Talk / Music (re-shoot engine, default), Scene (your angles), Dub (multicam inserts) | Re-shoots a 5 s slice of your **real take** from a new virtual camera | ~$0.055 per clip |
| [`idlora_api.json`](idlora_api.json) | Talk / Music (classic engine), Scene (partner B's lines and the B-listening clip) | Turns one still and one voice reference into a talking clip, with identity and voice kept | ~$0.033 per clip |
| [`qwen_angles_api.json`](qwen_angles_api.json) | Classic engine: the 8 camera-angle stills | Turns one frame into 8 angle stills (close-up, wide, ±45°, ±90°, low, aerial) | ~$0.03–0.05 per job |
| [`scene_edit_api.json`](scene_edit_api.json) | Scene: partner stills | The same Qwen graph with all 8 branch prompts and images exposed, for 8 free-form edits in one job | ~$0.03–0.05 per job |
| [`dub_api.json`](dub_api.json) | Dub: CAM A in the new language | LipDub/DubIt IC-LoRA: your take with new speech and matching lips | ~$0.09–0.11 per 8–10 s window |
| [`translate_api.json`](translate_api.json) | Dub: translation | Gemma 3 12B translates the numbered transcript lines (core `TextGenerate` node) | ~$0.01 |

Suggested names map to these files as follows: CrossView camera angle = `crossview_api.json`, ID LoRA identity =
`idlora_api.json`, lip-sync = `dub_api.json`. The app doesn't use InfiniteTalk. Dub lip-sync runs on the LTX-2.3
LipDub (now "DubIt") IC-LoRA, and Scene's partner lip-sync comes from ID LoRA, which generates speech and lips in
the same pass. The file names stay the same because the code loads them by name (`MULTICAM_WORKFLOWS` can point
the app at another folder).

`build_v2v.py` regenerates `crossview_api.json` (`python workflows/build_v2v.py`) and `dub_api.json`
(`python workflows/build_v2v.py dub`) from the LTX IC-LoRA example graphs. `translate_api.json` is an export of
`multicam/dub.py:translate_workflow()`, with placeholder lines. The app builds that graph in code for each dub.

## Models

Put the files in the usual ComfyUI folders: `checkpoints/`, `loras/`, `text_encoders/`, `latent_upscale_models/`,
`diffusion_models/` and `vae/`. Comfy Cloud already has all of them. The exact file names in the graphs are the
Comfy Cloud names. A few are re-packs that use a slightly different name on Hugging Face; rename the file or
re-pick it in the loader node.

| Model (file name in the graphs) | Used in | Get it | License |
|---|---|---|---|
| LTX-2.3 22B dev fp8 (`ltx-2.3-22b-dev-fp8.safetensors`): video model, audio VAE, and the checkpoint half of the text encoder | crossview, idlora, dub, translate | [Lightricks/LTX-2.3-fp8](https://huggingface.co/Lightricks/LTX-2.3-fp8) (bf16: [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3)) | LTX-2 Community License (open weights) |
| LTX-2.3 distilled 1.1 LoRA (`ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors`), strength 0.6 (crossview) / 0.5 (dub, idlora) | crossview, idlora, dub | [Comfy-Org/ltx-2.3](https://huggingface.co/Comfy-Org/ltx-2.3) `split_files/loras/` | LTX-2 Community License |
| LTX-2.3 spatial upscaler x2 1.1 (`ltx-2.3-spatial-upscaler-x2-1.1.safetensors`) | crossview, idlora, dub | [Lightricks/LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) | LTX-2 Community License |
| CrossView IC-LoRA v0.9 (`LTX2.3-22B_IC-LoRA-CrossView-Prompt_v0.9_13700.safetensors`), strength 1.5 | crossview | [Cseti/LTX2.3-22B_IC-LoRA-CrossView-Prompt](https://huggingface.co/Cseti/LTX2.3-22B_IC-LoRA-CrossView-Prompt) | Apache-2.0 |
| ID LoRA TalkVid 3k (`ltx-2.3-id-lora-talkvid-3k.safetensors`), strength 1.0 | idlora | [Comfy-Org/ltx-2.3](https://huggingface.co/Comfy-Org/ltx-2.3) `split_files/loras/`; project: [ID-LoRA](https://github.com/ID-LoRA/ID-LoRA) | [LTX-2 Community License](https://github.com/ID-LoRA/ID-LoRA/blob/main/LICENSE) |
| LipDub / DubIt IC-LoRA 0.9 (`ltx-2.3-22b-ic-lora-lipdub-0.9.safetensors`; on HF `ltx-2.3-22b-ic-lora-dubit-0.9.safetensors`), strength 1.0 | dub | [Lightricks/LTX-2.3-22b-IC-LoRA-DubIt](https://huggingface.co/Lightricks/LTX-2.3-22b-IC-LoRA-DubIt) | LTX-2 Community License |
| Gemma 3 12B it text encoder, fp4 mixed (`gemma_3_12B_it_fp4_mixed.safetensors`) | crossview, idlora, dub (prompt encoding) | [Comfy-Org/ltx-2](https://huggingface.co/Comfy-Org/ltx-2) `split_files/text_encoders/` | Gemma Terms of Use |
| Gemma 3 12B it bf16 (`gemma_3_12B_it.safetensors`); bf16 because the fp4 build made spelling mistakes when translating | translate | [Comfy-Org/ltx-2](https://huggingface.co/Comfy-Org/ltx-2) `split_files/text_encoders/` ([google/gemma-3-12b-it](https://huggingface.co/google/gemma-3-12b-it)) | Gemma Terms of Use |
| Qwen-Image-Edit 2511 bf16 (`qwen_image_edit_2511_bf16.safetensors`) | qwen_angles, scene_edit | [Comfy-Org/Qwen-Image-Edit](https://huggingface.co/Comfy-Org/Qwen-Image-Edit) `split_files/diffusion_models/` ([Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511)) | Apache-2.0 |
| Qwen 2.5 VL 7B fp8 (`qwen_2.5_vl_7b_fp8_scaled.safetensors`) + Qwen-Image VAE (`qwen_image_vae.safetensors`) | qwen_angles, scene_edit | [Comfy-Org/Qwen-Image](https://huggingface.co/Comfy-Org/Qwen-Image) `split_files/text_encoders/`, `split_files/vae/` | Apache-2.0 |
| Qwen-Image-Edit 2511 Lightning 4-step LoRA (`Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors`) | qwen_angles, scene_edit | [lightx2v/Qwen-Image-Edit-2511-Lightning](https://huggingface.co/lightx2v/Qwen-Image-Edit-2511-Lightning) | Apache-2.0 |
| Multiple-Angles LoRA (`qwen-image-edit-2511-multiple-angles-lora.safetensors`) | qwen_angles, scene_edit | [fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA](https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA) | Apache-2.0 |

The LTX nodes (`LTXICLoRALoaderModelOnly`, `LTXAddVideoICLoRAGuide`, `LTXVSetAudioRefTokens`, `LTXVReferenceAudio`, ...)
and `TextGenerate` are in current ComfyUI core and the bundled LTX nodes. Update ComfyUI if any node shows up red.

## What the app sets (bindings)

### `crossview_api.json`: CrossView re-shoot (LTX-2.3 22B fp8 + distilled LoRA 0.6 + CrossView IC-LoRA 1.5)

The real 5.04 s slice of your take (121 frames at 24 fps) is the IC-LoRA reference video. Its audio is frozen
into the audio latent (`LTXVSetAudioRefTokens`), so frame *t* of the clip is frame *t* of the source, with the
same head motion and the same lips. The prompt only describes the camera move. Stage 1 runs 8 steps at half
resolution, `LTXVLatentUpsampler` doubles it, and stage 2 runs 3 steps at 704×1280.

| Binding | Node.input | Set to |
|---|---|---|
| `video` | `7.file` (LoadVideo) | the uploaded reference slice |
| `prompt` | `11.text` | `crossview. new camera angle: <azimuth>, <elevation>, <distance>.` (presets in `multicam/reshoot.py`, e.g. `far to the right, same height, closer`) |
| `seed` | `24.noise_seed`, `50.noise_seed` | one seed per camera, fixed across that camera's inserts |
| `lat_w`, `lat_h` | `14.width`, `14.height` | half the output size (352×640 for 704×1280) |

### `idlora_api.json`: LTX-2.3 ID LoRA (Comfy Cloud template "LTX-2.3: ID LoRA")

| Binding | Node.input | Set to |
|---|---|---|
| `image` | `269.image` | the angle still (classic) or the partner still (Scene) |
| `audio` | `276.audio` | the voice reference: your real audio slice, or a Kokoro-82M sample for a non-clone partner |
| `prompt` | `340:319.value` | `[VISUAL]: … [SPEECH]: <the exact words> [SOUNDS]: …` |
| `width`, `height` | `340:330.value`, `340:324.value` | 720 / 1280 |
| `fps`, `duration` | `340:323.value`, `340:331.value` | 24 / 5.0 |
| `seed` | `340:286.noise_seed` | random per job |

### `qwen_angles_api.json`: Qwen 1 Click Multiple Character Angles (Comfy Cloud template)

`image` goes to `25.image` (one frame of your take), and the job returns 8 stills. `_outputs` in the bindings
names each SaveImage node (`closeup`, `wide`, `left45`, `right90`, ...). The `seed` binding exists, but the app
keeps the template seeds.

### `scene_edit_api.json`: Qwen-Image-Edit 2511, 8 free-form edits in one job

The same graph as `qwen_angles` with every branch exposed. `image` sets all 8 inputs, and `img1`..`img8`
override one branch each. `p1`..`p8` are the edit prompts (`TextEncodeQwenImageEditPlus.prompt`), `s1`..`s8`
are the seeds, `l1`..`l8` set the Multiple-Angles LoRA strength per branch, and `angle_lora` sets it on all
branches (0 = plain Qwen-Image-Edit). Scene uses it for partner B single, over-the-shoulder and close-up stills.

### `dub_api.json`: LipDub / DubIt (LTX-2.3 22B fp8 + distilled LoRA 0.5 + LipDub IC-LoRA 1.0)

The real take is the IC reference at downscale 1. Its audio only conditions the voice, and new speech is
generated from the prompt. Stage 2 freezes the stage-1 speech and refines the video.

| Binding | Node.input | Set to |
|---|---|---|
| `video` | `7.file` | the uploaded window of your take (8–10 s) |
| `prompt` | `11.text` | the translated line(s) for that window |
| `seed` | `24.noise_seed`, `50.noise_seed` | per window |
| `lat_w`, `lat_h` | `14.width`, `14.height` | half the output size |
| `frames` | `14.length`, `31.frames_number` | window length in frames (8k+1) |

### `translate_api.json`: Gemma 3 12B translation

`prompt` → `2.prompt`: the instruction plus your numbered sentences. The reply is the text output of
`PreviewAny` (node 3), parsed back line by line (`multicam/dub.py:parse_numbered`). `sampling_mode: off` gives
greedy decoding, so a re-run returns the same translation.

## Run one yourself

**Local ComfyUI (your GPU).** Install the models above, start ComfyUI and load the file. The editor opens
API-format JSON too: drag it in or use Workflow → Open. Replace the placeholder inputs (`ref.mp4`, the image and
the audio) with your own files, edit the prompt and seed, and queue it. The LTX graphs run a 22B model, so they need a
high-VRAM GPU (or ComfyUI's model offloading). You can also skip the editor and POST it:

```bash
python - <<'PY'
import json, urllib.request
wf = json.load(open("workflows/crossview_api.json"))
wf["7"]["inputs"]["file"] = "my_take_5s.mp4"          # a file in ComfyUI/input/
wf["11"]["inputs"]["text"] = "crossview. new camera angle: far to the right, same height, closer."
req = urllib.request.Request("http://127.0.0.1:8188/prompt", data=json.dumps({"prompt": wf}).encode(),
                             headers={"Content-Type": "application/json"})
print(urllib.request.urlopen(req).read().decode())
PY
```

**Comfy Cloud.** Use your own API key from <https://platform.comfy.org/profile/api-keys>. It's read from the
environment and never written anywhere. `multicam comfy run` uploads any file path you pass with `--set`,
patches the bindings, submits, waits and downloads the outputs:

```bash
export COMFY_API_KEY=...   # never commit this
multicam comfy run --workflow workflows/crossview_api.json --bindings workflows/crossview_bindings.json \
  --set video=my_take_5s.mp4 --set "prompt=crossview. new camera angle: far to the right, same height, closer." \
  --set seed=42 --set lat_w=352 --set lat_h=640 --out-dir out/
multicam comfy run --workflow workflows/qwen_angles_api.json --bindings workflows/qwen_angles_bindings.json \
  --set image=frame.png --out-dir angles/
multicam comfy run --workflow workflows/idlora_api.json --bindings workflows/idlora_bindings.json \
  --set image=angles/right90.png --set audio=slice.wav --set "prompt=[VISUAL]: ... [SPEECH]: ..." --out-dir out/
# add --dry-run to print the patched inputs without calling the API
```

The higher-level commands run the same files end to end: `multicam reshoot` (crossview), `multicam comfy submit`
(idlora jobs from `plan`/`prep`), `multicam dub` (translate + dub) and `multicam scene …` (scene_edit + idlora +
crossview). In the web app, each finished job also has a **⬇ ComfyUI workflow** link: the exact patched graph
that ran for that clip, ready to re-queue.
