---
name: lit-ask
description: Answer questions about the analyzed paper corpus, with citations back to specific papers and pages. Use when the user says "/lit-ask", asks a question about their papers ("what does the literature say about X", "which papers use dataset Y", "who disagrees about Z"), or wants to find papers matching criteria.
---

# lit-ask

Question answering over `.lit/papers/`. `scripts/ask.py` retrieves; **you read and answer**.

Let `PY` be `~/.lit-agent/venv/Scripts/python.exe` on Windows, `~/.lit-agent/venv/bin/python`
elsewhere. Let `S` be `${CLAUDE_PLUGIN_ROOT}/scripts`.

## The two rules

1. **Every claim in your answer carries a citekey and a locator.** The notes already carry
   page locators; propagate them. An answer without citations is not an answer here.
2. **If the corpus does not support an answer, say so.** Do not fall back on what you know
   about the topic. The user is asking what *their library* says, and a fluent answer sourced
   from your own training is the failure mode this whole project exists to prevent.

## Retrieve

```
PY "S/ask.py" "the user's question in plain words" --top 8
```

| Flag | Use |
|---|---|
| `--filter relevance=high year>=2020` | narrow by frontmatter before scoring |
| `--list` | show the filtered corpus without a query |
| `--top N` | how many notes to consider |
| `--json` | when you need to branch on the result |

Filters accept `=`, `!=`, `>`, `<`, `>=`, `<=` over any frontmatter field: `relevance`,
`year`, `paper_type`, `scope_tags`, `methods`, `datasets`, `venue`, `confidence`.
List-valued fields match if any element matches.

## Read before answering

**Retrieval is a shortlist, not an answer.** The scores rank notes by keyword overlap, and
keyword overlap cannot tell "this paper answers the question" from "this paper is about the
same topic". Open the top notes and read the relevant sections.

This matters most for questions the corpus *cannot* answer. Ask "what is PolicyLint's
throughput in policies per second" and retrieval will confidently return the PolicyLint note —
because the words match — even though the note says nothing about throughput. Only reading
catches that.

So: read, then decide whether the notes actually contain the answer. If they do not, say
which papers you checked and what they cover instead.

## Answer

- Lead with the answer, not with a description of your search.
- Cite as `[@citekey, p. N]`, propagating the locator from the note rather than inventing one.
- When papers disagree, **say so and give both sides with their locators** — that is more
  useful than a synthesized average that misrepresents both.
- Distinguish what a paper *reports* from what the analyzer *observed about it*. The notes
  keep these separate in section 7; keep them separate in your answer.
- Flag anything marked `[UNVERIFIED]` in a note you are relying on.
- If a note's `confidence` is not `high`, or its `scope_version` differs from the current
  scope, mention it.

## When the corpus cannot answer

Say it plainly, then be useful:

> Nothing in your corpus addresses this. The closest papers are X, which covers A, and Y,
> which covers B — neither reports C.

Then offer the two things that actually help: record it as a gap for `gaps.md`, or widen the
search (the corpus may hold relevant papers that keyword search missed — try different
vocabulary, or the competing terminology from the scope block).

**Never bridge the gap with your own knowledge of the field**, even when you are confident
and even when the user pushes. If they want general background, they can ask for it
explicitly, and you should say clearly that it is not sourced from their library.

## Scale

Grep retrieval is the default and works well past 150 papers. If the corpus is much larger
and recall seems poor, the `vector_index` capability enables hybrid retrieval — but check
whether the problem is really vocabulary mismatch first, which the scope block's competing
terminology usually fixes more cheaply.
