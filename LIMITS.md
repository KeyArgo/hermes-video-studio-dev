# Limits

What Hewn Cut does not do. Written down because a demo is only believable if the
edges are stated.

- **One video track.** No picture-in-picture, no overlays on top of the footage
  beyond burned captions. Two sources means two renders and an external concat.
- **No transitions.** Cuts are cuts. There is no crossfade, wipe or dip because
  the brand grammar has no transitions either.
- **No keyframes.** Speed, mute and captions apply to a range, not a curve.
- **Captions are burned in, not a subtitle track.** Re-cutting after a caption
  change means a re-render, and a player cannot turn them off.
- **No speech recognition.** `tighten` finds *silence*, not words; it cannot cut
  "um" without deleting the pause around it. There is no transcript-based edit.
- **No colour, no audio mixing beyond one loudness pass.** No EQ, no music bed,
  no ducking under a second track.
- **Times are absolute seconds on the current timeline.** A snip shifts every
  later time; captions added before a snip are not shifted with it. Add captions
  after the cut is locked.
- **Rendering is a full re-encode.** A 90 s cut takes ~2 minutes on this machine
  (crf 18, veryfast). It is not a smart-render or a proxy workflow.
- **ffmpeg must exist.** There is no bundled encoder and no fallback.
