# Session 10 — 18:40, 27-09-2026
## Offline narrated demo video

**Goal:** A walkthrough video explaining the deck and every feature, built
entirely offline (no network, no browser automation available in this
environment).

**What was built:** A from-scratch offline text-to-speech pipeline using
`espeak-ng` via `ctypes` (no gTTS/cloud dependency, since outbound network
access to speech APIs was blocked here), a 15-segment narration script
covering the whole deck, and an `ffmpeg` assembly step syncing each
rendered slide image to its narration clip before concatenating everything
into one MP4.

**Outcome:** A ~9-minute narrated walkthrough video, spot-checked by
grabbing frames at several timestamps and confirming they land on the
correct slide — the attached screenshot is the actual final frame of that
rendered video.
