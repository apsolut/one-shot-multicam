"""Build API-format LTX-2.3 IC-LoRA video-to-video workflows (reference video + frozen real audio).

Regenerate workflows/crossview_api.json:  python workflows/build_v2v.py
"""
import json

CKPT = "ltx-2.3-22b-dev-fp8.safetensors"
DIST = "ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors"
LORAS = {"crossview": "LTX2.3-22B_IC-LoRA-CrossView-Prompt_v0.9_13700.safetensors",
         "union": "ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
         "lipdub": "ltx-2.3-22b-ic-lora-lipdub-0.9.safetensors"}
SIG8 = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
SIG3 = "0.909375, 0.725, 0.421875, 0.0"


def build(ic="crossview", ic_strength=1.5, dist_strength=0.6, first_frame=False, ff_strength=1.0,
          id_lora=False, two_stage=False, width=544, height=960, frames=121, fps=24, guide_strength=1.0,
          sampler="euler_ancestral_cfg_pp", control="video", ff_mode="condonly", audio="frozen",
          ref_downscale=None):
    """audio="frozen": the real audio is locked into the clip (re-shoot: frame t = source frame t).
    audio="generate": new speech is generated (LipDub/Dub-It): the real audio only conditions the voice
    (reference tokens); stage 2 then freezes the stage-1 speech and refines the video.
    ref_downscale: fixed IC reference downscale (LipDub was trained at 1); None = the LoRA's own factor."""
    W = {}
    def n(i, cls, **inp):
        W[str(i)] = {"class_type": cls, "inputs": inp}
        return str(i)
    ck = n(1, "CheckpointLoaderSimple", ckpt_name=CKPT)
    avae = n(2, "LTXVAudioVAELoader", ckpt_name=CKPT)
    te = n(3, "LTXAVTextEncoderLoader", text_encoder="gemma_3_12B_it_fp4_mixed.safetensors", ckpt_name=CKPT, device="default")
    m = n(4, "LoraLoaderModelOnly", lora_name=DIST, strength_model=dist_strength, model=[ck, 0])
    if id_lora:
        m = n(5, "LoraLoaderModelOnly", lora_name="ltx-2.3-id-lora-talkvid-3k.safetensors", strength_model=1.0, model=[m, 0])
    ic_l = n(6, "LTXICLoRALoaderModelOnly", lora_name=LORAS[ic], strength_model=ic_strength, model=[m, 0])
    vid = n(7, "LoadVideo", file="ref.mp4")
    comp = n(8, "GetVideoComponents", video=[vid, 0])
    ctl = n(9, "LoadVideo", file="ctl.mp4") if control == "separate" else vid
    ctl_c = n(10, "GetVideoComponents", video=[ctl, 0]) if control == "separate" else comp
    if control == "dwpose":
        dw = n(19, "DWPreprocessor", image=[comp, 0], detect_hand="enable", detect_body="enable", detect_face="enable",
               resolution=512, bbox_detector="yolox_l.onnx", pose_estimator="dw-ll_ucoco_384_bs5.torchscript.pt",
               scale_stick_for_xinsr_cn="disable")
        ctl_c = dw
    pos = n(11, "CLIPTextEncode", text="PROMPT", clip=[te, 0])
    neg = n(12, "CLIPTextEncode", text="pc game, console game, video game, cartoon, childish, ugly, deformed face, extra fingers", clip=[te, 0])
    cond = n(13, "LTXVConditioning", frame_rate=float(fps), positive=[pos, 0], negative=[neg, 0])
    sw, sh = (width // 2, height // 2) if two_stage else (width, height)
    lat = n(14, "EmptyLTXVLatentVideo", width=sw, height=sh, length=frames, batch_size=1)
    if first_frame:
        img = n(15, "LoadImage", image="first.png")
        rs = n(16, "ResizeImageMaskNode", **{"resize_type": "scale dimensions", "resize_type.width": sw, "resize_type.height": sh,
                                            "resize_type.crop": "center", "scale_method": "lanczos", "input": [img, 0]})
        pre = n(17, "LTXVPreprocess", img_compression=18, image=[rs, 0])
        if ff_mode == "inplace":
            lat = n(18, "LTXVImgToVideoInplace", vae=[ck, 2], image=[pre, 0], latent=[lat, 0], strength=ff_strength, bypass=False)
        else:
            lat = n(18, "LTXVImgToVideoConditionOnly", vae=[ck, 2], image=[pre, 0], latent=[lat, 0], strength=ff_strength, bypass=False)
    guide = n(20, "LTXAddVideoICLoRAGuide", positive=[cond, 0], negative=[cond, 1], vae=[ck, 2], latent=[lat, 0],
              image=[ctl_c, 0], frame_idx=0, strength=guide_strength,
              latent_downscale_factor=[ic_l, 1] if ref_downscale is None else float(ref_downscale), crop="center",
              use_tiled_encode=False, tile_size=256, tile_overlap=64)
    aenc = n(21, "LTXVAudioVAEEncode", audio=[comp, 1], audio_vae=[avae, 0])
    art = n(22, "LTXVSetAudioRefTokens", positive=[guide, 0], negative=[guide, 1], audio_latent=[aenc, 0])
    model = [ic_l, 0]
    p_, n_ = [art, 0], [art, 1]
    if audio == "generate":
        a_lat = n(31, "LTXVEmptyLatentAudio", frames_number=frames, frame_rate=float(fps), batch_size=1, audio_vae=[avae, 0])
        av = n(23, "LTXVConcatAVLatent", video_latent=[guide, 2], audio_latent=[a_lat, 0])
    else:
        av = n(23, "LTXVConcatAVLatent", video_latent=[guide, 2], audio_latent=[art, 2])
    noise = n(24, "RandomNoise", noise_seed=42)
    smp = n(25, "KSamplerSelect", sampler_name=sampler)
    sig = n(26, "ManualSigmas", sigmas=SIG8)
    g = n(27, "CFGGuider", cfg=1, model=model, positive=p_, negative=n_)
    out = n(28, "SamplerCustomAdvanced", noise=[noise, 0], guider=[g, 0], sampler=[smp, 0], sigmas=[sig, 0], latent_image=[av, 0])
    sep = n(29, "LTXVSeparateAVLatent", av_latent=[out, 0])
    crop = n(30, "LTXVCropGuides", positive=[guide, 0], negative=[guide, 1], latent=[sep, 0])
    vlat, alat = [crop, 2], [sep, 1]
    if two_stage:
        up_m = n(40, "LatentUpscaleModelLoader", model_name="ltx-2.3-spatial-upscaler-x2-1.1.safetensors")
        up = n(41, "LTXVLatentUpsampler", samples=vlat, upscale_model=[up_m, 0], vae=[ck, 2])
        lat2 = [up, 0]
        if first_frame:
            pre2 = n(42, "LTXVPreprocess", img_compression=18, image=[n(43, "ResizeImageMaskNode", **{"resize_type": "scale dimensions",
                     "resize_type.width": width, "resize_type.height": height, "resize_type.crop": "center",
                     "scale_method": "lanczos", "input": ["15", 0]}), 0])
            lat2 = [n(44, "LTXVImgToVideoInplace", strength=1.0, bypass=False, vae=[ck, 2], image=[pre2, 0], latent=lat2), 0]
        art2 = n(45, "LTXVSetAudioRefTokens", positive=[crop, 0], negative=[crop, 1], audio_latent=[sep, 1])
        guide2 = n(46, "LTXAddVideoICLoRAGuide", positive=[art2, 0], negative=[art2, 1], vae=[ck, 2], latent=lat2,
                   image=[ctl_c, 0], frame_idx=0, strength=guide_strength,
                   latent_downscale_factor=[ic_l, 1] if ref_downscale is None else float(ref_downscale), crop="center",
                   use_tiled_encode=True, tile_size=256, tile_overlap=64)
        av2 = n(47, "LTXVConcatAVLatent", video_latent=[guide2, 2], audio_latent=[art2, 2])
        g2 = n(48, "CFGGuider", cfg=1, model=model, positive=[guide2, 0], negative=[guide2, 1])
        out2 = n(49, "SamplerCustomAdvanced", noise=[n(50, "RandomNoise", noise_seed=43), 0], guider=[g2, 0],
                 sampler=[n(51, "KSamplerSelect", sampler_name="euler"), 0], sigmas=[n(52, "ManualSigmas", sigmas=SIG3), 0],
                 latent_image=[av2, 0])
        sep2 = n(53, "LTXVSeparateAVLatent", av_latent=[out2, 0])
        crop2 = n(54, "LTXVCropGuides", positive=[guide2, 0], negative=[guide2, 1], latent=[sep2, 0])
        vlat, alat = [crop2, 2], [sep2, 1]
    dec = n(60, "VAEDecodeTiled", tile_size=768, overlap=64, temporal_size=4096, temporal_overlap=4, samples=vlat, vae=[ck, 2])
    adec = n(61, "LTXVAudioVAEDecode", samples=alat, audio_vae=[avae, 0])
    cv = n(62, "CreateVideo", fps=float(fps), images=[dec, 0], audio=[adec, 0])
    n(63, "SaveVideo", filename_prefix="video/multicam_v2v", format="auto", codec="auto", video=[cv, 0])
    bindings = {"video": [["7", "file"]], "prompt": [["11", "text"]], "seed": [["24", "noise_seed"]]}
    if control == "separate":
        bindings["control"] = [["9", "file"]]
    if first_frame:
        bindings["image"] = [["15", "image"]]
    return W, bindings


def dub_workflow(width=704, height=1280, frames=121, fps=24):
    """Dub mode: LTX-2.3 LipDub IC-LoRA (Lightricks' official Dub-It settings: IC 1.0, distilled 0.5, ref downscale 1)."""
    return build(ic="lipdub", ic_strength=1.0, dist_strength=0.5, two_stage=True, width=width, height=height,
                 frames=frames, fps=fps, audio="generate", ref_downscale=1, sampler="euler")


if __name__ == "__main__":  # print a shipped workflow: crossview (default) or dub
    import sys
    if sys.argv[1:] == ["dub"]:
        w, _ = dub_workflow()
    else:
        w, _ = build(ic="crossview", ic_strength=1.5, dist_strength=0.6, two_stage=True, width=704, height=1280)
    print(json.dumps(w, indent=1))
