// Gallery config. Video files are NOT in git: upload them to a GitHub Release (or any static host)
// and point these URLs at them, or drop them into docs/media/ locally.
// e.g. "https://github.com/<you>/multicam/releases/download/v0.1.0/demo_v5.mp4"
window.MULTICAM_GALLERY = {
  repo: "https://github.com/apsolut/one-shot-multicam",
  items: [
    {
      title: "“I used to lose hours debugging…”",
      subtitle: "17.6 s selfie · 4 AI inserts (2× CAM B profile, 2× CAM C wide) · talk mode",
      original: "media/selfie.mp4",
      multicam: "media/demo_v5.mp4",
      labeled: "media/demo_v5_labeled.mp4",
      sync: "media/sync_check_v5.mp4",
      poster: "media/poster.jpg",
      cuts: [ // [cam, start, end] from examples/clips_aligned + plan
        ["A", 0, 2.167], ["C", 2.167, 3.5], ["A", 3.5, 5.833], ["B", 5.833, 6.9], ["A", 6.9, 11.133],
        ["C", 11.133, 12.667], ["A", 12.667, 14.433], ["B", 14.433, 15.933], ["A", 15.933, 17.567]
      ],
      cost: "4 Comfy Cloud jobs ≈ $0.13"
    }
  ]
};
