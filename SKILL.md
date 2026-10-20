---
name: subtitle-to-study-notes
description: Convert a knowledge or teaching video's subtitle or transcript file (SRT, VTT, ASS/SSA, LRC, or plain text) into structured Markdown study notes built for someone who has NOT watched the video — with a concept map up front, per-concept conclusion cards, explicit emphasis, corrected ASR terminology, and board/diagram content rebuilt from what the speech supports. Use when the input is a subtitle or transcript artifact for an explanatory or instructional video and the goal is material a reader can actually learn from. Do not use for meeting minutes, general video summaries, live or real-time transcription, or entertainment and narrative videos.
---

# Subtitle to Study Notes

Turn a lecture's subtitle file into notes that teach. The reader has not watched
the video: they need to know what matters, in what order to take it in, and how
to get back to the video when the written form is not enough.

Two things are equally protected: the notes stay **faithful** to what the speaker
actually said, and they stay **usable** as a standalone study object. Where those
pull apart, see `references/note-format.md`.

## Use / do not use

Use when: the input is a subtitle or transcript file for an explanatory or
instructional video (lecture, course, tutorial, technical talk) and the goal is
notes worth keeping.

Do not use for: meeting minutes, standups, interviews; a general "what was this
video about" summary; live or streaming transcription (this works on a finished
file); vlog, gaming, or narrative content with no teaching intent. If the
material is one of these, say so instead of producing notes.

## Workflow

**1. Segment the transcript.** Run the parser on the input file:

```bash
python "<this-skill-dir>/scripts/parse_subtitles.py" "<subtitle-file>"
```

Resolve `<this-skill-dir>` to wherever this skill actually lives — do not assume
the working directory is the skill directory. The script reads UTF-8 (and
GBK-encoded) files and writes to stdout, so it is safe to run from anywhere; on
Windows use a proper `python` and quote both paths, since Chinese file names are
the norm here.

It detects encoding and format on its own, merges fragmentary cues into
order-preserving paragraphs, and prints a numbered transcript plus five evidence
blocks. `--json` gives the same data structured. If the defaults misjudge this
speaker, tune segmentation with `--gap`, `--max-chars`, or `--soft-min` rather
than writing your own subtitle parser.

**2. Repair terminology.** Read `references/asr-correction.md` and apply its
gates. The parser's candidate list is evidence, not a decision: its absence
proves nothing, and it never contains a replacement instruction.

**3. Write the notes.** Read `references/note-format.md` first — it carries the
full skeleton, the emphasis rules, and the reconstruction notation. Follow the
speaker's concept order as given by the transcript's paragraphs.

**4. Check before delivering** against the list at the end of this file.

## Non-negotiables

- **Concept order is the speaker's.** Never move a concept earlier or later,
  never merge two concepts into one, never split one across two places. The only
  permitted organisation is **grouping consecutive concepts into `##` 板块**.
  Consequence: heading timestamps stay strictly increasing — if they do not, you
  moved something.
- **Two heading levels only**: `##` is a 板块, `###` is a concept.
- **Emphasis comes from exactly two sources**: what the speaker stressed, and
  what the speaker defined. Bounds, examples, contrasts, counter-examples and
  quantity relations stay in the body prose and are never promoted — except
  numbers that are *part of a definition*, and bare quantities the speaker
  himself summed up with a stress marker. Never invent a third source.
- **Compress expression, never content.** You may drop filler, repetition, and
  the step-by-step working of an example. You may not drop the example itself,
  a causal link, a contrast, or a counter-example.
- **Add no knowledge from outside the video**, with one exception: a minimal
  bridging note, always marked `[补充] <one sentence>`, only where the speaker
  assumes background the reader cannot have. Keep it to 0–5 occurrences; do not
  amplify it into teaching material.
- **Output Markdown.**
- **Rebuilt visuals carry the exact prefix** `> **板书重建（非原视频画面）**：`
  Every reconstruction must be unmistakable and supported by the speech. Where
  it cannot be, write `原图未出现在字幕中，需回看视频 <link>`.
- **Timestamps are jump links** when a Bilibili BV/av id is recognisable:
  `[00:08:16](https://www.bilibili.com/video/<id>?t=496.0)`. Verify the id really
  is this video before emitting any link — the script only pattern-matches it,
  and a wrong id sends every timestamp somewhere else. No id → plain `[00:08:16]`.
- **Include by default**: 学习地图 (top), per-concept 结论卡, 术语表, 结构图.
- **Never include**: a formula or property summary table; self-test questions; a
  whole-note "core conclusions" digest; a whole-note "common mistakes" digest.
  These are excluded by the user's specification. Produce them only on an
  explicit later request, as separate output.

## Output

Write one Markdown file named `<video title>_学习笔记.md`, in the same folder as
the input subtitle unless the user names a location. The video title is the
subtitle file name with its platform noise removed (e.g. a trailing
`_哔哩哔哩_bilibili_BV…_字幕`), not invented. Report the file path when done.

## Note skeleton

```markdown
# <video title> 学习笔记
> 来源：<subtitle file name> ｜ 时长 <HH:MM:SS> ｜ 由字幕整理，不含原视频画面

## 学习地图            (路线一段话 + mermaid + 推荐阅读顺序)
## 一、<板块名>
### 1.1 <概念名> [00:00:16](<跳转链接>)
### 1.2 <概念名> [00:01:00](<跳转链接>)
## 二、<板块名>
### 2.1 <概念名> [00:02:01](<跳转链接>)
---
## 术语表
## 结构图              (mermaid)
```

Each `###` concept holds: a `> **本节结论**：` card, then body prose, then an
optional `**关键点**` list. 2–5 板块 for a 20-minute talk, at least two concepts
each. `references/note-format.md` has the full rules, the emphasis split, and
worked examples.

## References

- `references/asr-correction.md` — read at step 2; mishearing types, the four
  decision gates, and the hard limits on correcting.
- `references/note-format.md` — read at step 3; full skeleton, emphasis rules,
  compression boundaries, the `[补充]` rule, jump-link format, visual
  reconstruction, worked examples.

## Check before delivering

- Concept order matches the transcript paragraph order, and every `###`
  timestamp is strictly increasing inside the video's duration.
- Exactly two heading levels; every 板块 holds at least two concepts.
- Every 结论卡 traces to a stress signal or a definition. No bound, example, or
  bare quantity relation was promoted into a card or a 关键点. (Numbers inside a
  definition, and quantities the speaker summed up with a stress marker, are
  allowed.)
- `[补充]` used 0–5 times, each one sentence, none contradicting the speaker.
- Non-key passages are compressed in expression while every example, causal
  link, contrast and counter-example survives.
- Every place the subtitles point at a picture has either a reconstruction with
  the required prefix or an explicit "回看视频" note.
- Jump links point at the right video id, and each `t=` equals its heading's
  `[HH:MM:SS]` converted to whole seconds (so a reader can verify one from the
  other).
- 学习地图, 术语表 and 结构图 present; formula table, self-test, and whole-note
  digests absent.
- Spot-check three concepts: the speaker's examples and causal links survived,
  and nothing was added that the speaker never said.

## Limits

Say so rather than forcing a result when:

- the video is a screen operation demo with almost no narration, so there is
  nothing to note;
- the talk leans entirely on visuals (geometry proofs, pure diagram work) and the
  speech cannot support reconstruction — the notes would be a shell;
- the subtitle file is truncated, badly out of sync, or mixes languages;
- the speaker is not a single presenter (streams, panel discussions, inserted
  ads) — segment anyway, but flag the parts that are probably not the main talk.

For very long inputs, prefer noting a coherent slice and saying which range you
covered over producing a thin pass across everything.
