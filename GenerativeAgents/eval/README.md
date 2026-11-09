# Evaluation: does memory make the agents better at representing us?

Same team, same floor, three runs that differ only in what the agents remember (the **memory**
selector in the simulation bar). Each of us rates our own agent's conversations, blind to the
condition, and an LLM judge rates the same conversations with the same rubric, so we can see where
human and LLM preferences diverge.

| memory | what each agent brings into a conversation |
| --- | --- |
| full | up to 3 retrieved memories, its history with this partner, ideas in play and from earlier runs |
| limited | 1 retrieved memory; no shared history; only this run's ideas |
| none | its profile and topic only; no ideas to build on |

Rubric, 1 (very low) to 5 (very high), from the point of view of the student whose agent talked:

| criterion | question |
| --- | --- |
| represents_me | did my agent represent me and my interests well? |
| novelty | is the insight or idea new to me? |
| usefulness | is it useful for my work? |
| relevance | is it relevant to my project and interests? |
| follow_up | would I follow up with this classmate about it? |

## Protocol (one person runs it, everyone rates)

1. `python serve.py` with `GEMINI_API_KEY` in `.env`, open the page, accept your profile.
2. Warm-up so "full" has something to remember: memory **full**, skip to the end. (run N)
3. Memory **none**, skip to the end (run N+1); then **limited** (N+2); then **full** (N+3).
   Changing the selector starts a new run; each saved run records its condition.
4. `python eval/evaluate.py sheets --runs N+1,N+2,N+3`
   writes `data/eval/latest/ratings_<code>.csv` (+ `.md`, easier to read) for each person.
5. Send everyone their CSV. Each fills the five columns with 1 to 5 (and an optional comment),
   without trying to guess the condition, and sends it back into that folder.
6. `python eval/evaluate.py judge` (Gemini, same rubric, never sees the condition; rerun to retry failures)
7. `python eval/evaluate.py report` -> `data/eval/latest/report.md`: ratings by memory condition for
   people and the judge, agreement per criterion (gap, Spearman ρ, same score, within 1 point), per
   person, and the largest disagreements with the judge's reason.

`data/` is git-ignored: the sheets contain our conversations. Copy the tables you want into the write-up.

## Limits

- N = 4 raters, one run per condition: report it as a pilot, not a significance test.
- Conversations with memory can mention "last time", so the condition is not perfectly hidden.
- The full run remembers the earlier runs, including the none and limited ones; that is what full memory means here.
