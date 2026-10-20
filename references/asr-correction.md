# Correcting ASR Errors in a Transcript

Read this when the transcript may contain auto-transcription errors — which is
almost always, for any machine-generated subtitle. The goal is to restore the
speaker's **terminology** without changing what they actually said.

## Why a replacement table does not work

A hand-written "wrong → right" dictionary is the obvious idea and it fails. Four
observed failure modes:

1. **One term, several wrong spellings.** A single concept can be mangled
   differently in different places, so no finite list covers it.
2. **The wrong form is itself a legitimate word.** Replacing globally will
   corrupt the places where that word is correct. *(A maths lecture may
   legitimately say 零点 / "zero point" in its own right; a lecture with English
   vocabulary may legitimately say "do".)*
3. **The correct form may never appear.** The speaker may never say the term
   clearly enough for the ASR to get it right even once, so there is nothing to
   align against.
4. **Errors are domain-specific.** Every field has its own vocabulary. A list
   built for one subject is dead weight for the next one — and this skill is
   meant to be general.

Conclusion: correction is a **judgement task**, not a lookup task.

## Division of labour

- The parser script (`scripts/parse_subtitles.py`) does the mechanical half: it
  reports *candidate* near-identical term pairs plus the Latin tokens that appear
  in the transcript. It never replaces anything.
- You do the semantic half: decide, from context and domain knowledge, which
  form is right — or whether to leave it alone.

The script's candidate list is a convenience, not a closed set. **A term's
absence from the list does not mean it is correct.** In particular, failure mode
3 above is invisible to the script: a lone wrong form with no surviving correct
counterpart produces no pair. Read the transcript for meaning; use the list as a
backstop.

## Mishearing types and how to judge each

| Type | What it looks like | How to judge it |
|---|---|---|
| **Homophone / near-homophone** | Two forms that sound alike but differ in one character or syllable | Prefer the form that dominates the transcript **and** fits the subject. A word appearing once against a well-attested twin is usually the error. |
| **Rare term swapped for a common word** | A familiar everyday word sitting in a technical slot | The register mismatch is the tell. If the sentence is a definition or a property and the word is banal, suspect it — then confirm the replacement actually belongs to this subject. |
| **English term misheard** | An acronym or Latin term rendered as an unrelated English-looking token | Use the surrounding cues: "…is short for…", "…also called…", "in English…". A list of terms defined in parallel often lets you repair one from its neighbours. |
| **Run-together / mis-split compound** | A standard compound term split or merged wrongly | Recover the standard compound only if the sentence's grammar and meaning require it. |

## Decision procedure

Apply these gates in order and stop at the first one that resolves the case.

1. **Self-consistency.** If one form clearly dominates the transcript and the
   other is a rare variant of the same sound/shape, align the rare one to the
   dominant one.
2. **Domain fit.** The replacement must belong to the subject's terminology. A
   substitution that is merely plausible-sounding is not enough — it has to be
   the term this field actually uses here.
3. **Protect legitimate words.** If the suspicious form is a valid term in its
   own right *and* the sentence reads correctly with it, **keep it**. Add a note
   only if the context is genuinely ambiguous.
4. **Confidence floor.** If no replacement is clearly right, keep the original
   text and mark it, e.g. ``[疑为转写误差：<原文>，建议核对 00:12:34]``. A flagged
   uncertainty is far more useful than a confident guess.

## Hard limits

- **Correct terminology only.** Do not reword, tighten, or "improve" an
  argument. If the speaker was vague, the notes stay vague.
- **Never invent content.** Do not add facts, definitions, examples, or
  conclusions the video does not contain — not even correct ones. The notes are a
  faithful record, not a textbook.
- **Do not build a subject glossary into the skill.** The only reliable source of
  truth is the transcript itself: what the speaker says repeatedly and what the
  surrounding sentences require. Record domain-independent *patterns* (the table
  above), never a term list.
- **Say so when you cannot tell.** Unresolved doubt belongs in the notes as a
  visible flag, not silently smoothed over.
