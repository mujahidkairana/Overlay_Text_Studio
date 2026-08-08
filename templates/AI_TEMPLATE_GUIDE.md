# Overlay timing file — instructions for AI models

Generate a UTF-8 CSV or XLSX with exactly one overlay per row. Copy the column
order from `overlay_template.csv`. Do not add commentary before or after the
table.

## Columns and allowed values

- `SCENE_ID`: unique stable ID such as `OVERLAY_001`.
- `START_TIME_30FPS`: inclusive `HH:MM:SS.FF`; `FF` must be `00` through `29`.
- `END_TIME_30FPS`: exclusive `HH:MM:SS.FF`, later than start and within video.
- `ON_SCREEN_TEXT`: concise viewer-facing text. Use at most two lines; encode an
  intentional line break as `\N`. Keep claims faithful to the supplied script.
- `TYPE`: `AUTO`, `QUESTION`, `FACT`, `NUMBER`, `EVIDENCE`, `COMPARISON`,
  `WARNING`, `UNCERTAINTY`, `TAKEAWAY`, or `SECTION`.
- `PRIORITY`: `LOW`, `MEDIUM`, or `HIGH`.
- `EMPHASIS_WORD`: optional exact word/short token occurring in the text.
- `VISUAL_ACTION`: `AUTO`, `NONE`, `PUNCH_IN`, `DIM_FOCUS`, or `FREEZE`.
- `SFX`: `NONE`, or the uppercase asset name for a licensed WAV file. For
  example, `SOFT_HIT` resolves to `soft_hit.wav` and `WHOOSH` to `whoosh.wav`.
- `LOCK_STYLE`: `TRUE` only when the requested style must not be automatically
  changed; otherwise `FALSE`.

## Recommended AI defaults

Use `TYPE=AUTO`, `PRIORITY=MEDIUM`, blank `EMPHASIS_WORD`,
`VISUAL_ACTION=AUTO`, `SFX=NONE`, and `LOCK_STYLE=FALSE` unless the supplied
script clearly justifies a more specific choice. Prefer short overlays, avoid
repeating captions word-for-word, do not invent facts, and do not schedule dense
back-to-back overlays. The app will automatically handle safe placement,
caption avoidance, animation, motion/detail suppression, density caps, and final
render verification.
