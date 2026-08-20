# M4 — worked Q&A over the gold set

Retrieval accuracy over the 15-question acceptance set in
`tests/fixtures/ask-questions.yaml` is **15/15**: every question surfaced an expected source
in the top 3. That measures retrieval. What follows measures the part that matters — whether
the *answers* carry correct citations, and whether the system declines when it should.

Answers below were produced from the six analyzed notes only.

---

## 1. "What is the reported precision of PolicyLint for contradiction detection?"

**There are two answers in your corpus, and they disagree by a wide margin.**

PolicyLint's own paper reports **97.3% precision (496/510)**, based on 14 false positives
found while manually validating 510 detected contradictions [@andownodatepolicylint, p. 16].

Goknil et al. re-ran PolicyLint on OPP-115 and hand-verified every hit. They report **24 true
positives out of 80 detections** — roughly 30% precision — and attribute the false positives
to PolicyLint's policy-simplification step discarding the conditions attached to a data
practice [@goknil2024privacy, p. 12].

The two are not straightforwardly reconcilable, but they agree on the *mechanism*: PolicyLint
itself states it "cannot extract the conditions or purposes behind collection and sharing
statements" [@andownodatepolicylint, p. 16], which is exactly what Goknil et al. blame. What
they disagree on is how much that costs in practice.

Three things could explain the gap, and the corpus does not settle which: different corpora
(Google Play policies versus OPP-115), different validation procedures (neither reports
inter-rater agreement), or different definitions of what counts as a contradiction.

> This is why the answer names both figures rather than averaging them. A synthesized
> "PolicyLint achieves 60–97% precision" would misrepresent both papers.

---

## 2. "Does prompt engineering outperform supervised baselines for privacy policy analysis?"

**No, on the evidence here — but fine-tuning does, and the two are routinely conflated.**

Chen et al. measured prompted Llama3-8B at **micro F1 0.694** against **0.730** for a prior
prompt-engineering study on the same OPP-115 category subset, and describe their prompting
results as "quite poor" [@chen2025llms, p. 7]. LoRA fine-tuning of the same 8B model reached
**0.916** on that subset [@chen2025llms, p. 7].

Goknil et al. reach a more favourable reading of prompting, reporting **F1 0.83** on OPP-115
annotation and positioning it as comparable to Polisis [@goknil2024privacy, p. 2]. Note the
comparison they draw against a supervised system: TLDR scores higher at 0.91, but trains on
80% of OPP-115, while their approach trains on none of it [@goknil2024privacy, p. 2].

These two are the sharpest disagreement in your corpus, and they are not independent — Chen
et al. adopt Goknil et al.'s best Llama3-8B result as their comparison point
[@chen2025llms, p. 7]. The disagreement is about interpretation and model scale, not about a
single contested measurement.

---

## 3. "Which data practice categories are hardest for automated classifiers?"

Consistently: **Data Retention, Data Security, Do Not Track**, and the catch-all *OTHER*
subcategories.

Chen et al. report the prompted model over-predicting on exactly these, producing high recall
and low precision, with the worked example of "We are committed to protecting and respecting
your privacy" being classified as Data Security despite describing no security measure
[@chen2025llms, p. 5]. Goknil et al.'s per-category results show Data Retention in the
0.23–0.57 range even for the strongest models [@goknil2024privacy, p. 8].

**The important qualifier is that humans find these categories hard too.** OPP-115's OTHER
class has a Fleiss kappa of just 0.49 among its three legal-expert annotators
[@chen2025llms, p. 5]. And the corpus paper itself cautions that Kappa is being applied to an
artificial task there, so the usual interpretive conventions do not directly transfer
[@wilson2016creation, p. 4]. Model performance on these categories should be read against
human agreement, not against 1.0.

---

## 4. "What is the throughput of PolicyLint in policies per second on GPU hardware?"

**Nothing in your corpus answers this.**

Retrieval surfaces the PolicyLint note with a high score, because the vocabulary matches —
but the note covers contradiction detection methodology, prevalence findings across 11,430
policies, and precision, not runtime or hardware. No paper in the six analyzed notes reports
throughput, latency, or hardware requirements for any of these systems.

The closest thing to a compute-cost statement in the corpus is Chen et al. noting that LoRA
fine-tuning was chosen over full fine-tuning for cost and run on a single RTX 4090
[@chen2025llms, p. 5] — which is training cost, not inference throughput, and for a different
system entirely.

Worth recording as a gap: none of the surveyed systems report the operational characteristics
you would need to deploy one.

> **This is the behaviour that matters most.** Retrieval returned a confident-looking hit.
> Only reading the note revealed it does not contain the answer. Answering from general
> knowledge of NLP pipeline throughput would have produced a fluent, plausible, unsourced
> answer — the exact failure P7 exists to prevent.

---

## What these examples demonstrate

| Behaviour | Shown in |
|---|---|
| Every claim carries citekey + page | all |
| Disagreements presented as disagreements, not averaged | 1, 2 |
| Analyzer observations kept distinct from paper claims | 1, 3 |
| Model performance contextualized against human agreement | 3 |
| Declining when the corpus cannot answer | 4 |
| Naming what *is* covered when declining | 4 |
