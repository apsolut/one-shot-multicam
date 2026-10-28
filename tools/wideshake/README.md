# Wide-shake investigation tools

These are the two scripts behind the 2026-10-28 "Wide shakes" review. The fix is in
`multicam/assemble.py`: synthetic shake is now off by default, and Wide inserts get a smooth `pullback_filter`.
Tests are in `tests/test_wide_pullback.py`.

- `motion.py VIDEO [T0 T1]` measures global motion from frame to frame (LK optical flow and a RANSAC similarity
  fit). It prints JSON with the mean |dx|/|dy|, the direction reversals, the scale per second, duplicate frames
  and the jitter in px. On the raw CrossView wide it showed ~0 px motion (locked off, no zoom). On the app render
  it showed back-and-forth 0/2 px steps, which was the synthetic shake.
- `make_demo.py RAW_CLIP FINAL SEG_START SEG_END [OUT_DIR]` writes a 3-up video (raw | app render | proposed
  fix) and a strip of frames with the motion trace.

They need `opencv-python` and `numpy` and are not part of the app. No media is committed: point them at
your own project's `gen/reshoot/*.mp4` and `final.mp4`.
