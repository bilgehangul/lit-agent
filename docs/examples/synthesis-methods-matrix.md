# Methods matrix

Built from note frontmatter plus the Evaluation section. A blank cell means the field was not recorded in the note, **not** that the paper lacks it.

| citekey | type | methods | datasets | metrics | headline result |
|---|---|---|---|---|---|
| `andownodatepolicylint` | systems | sentence-level NLP with negation handling; automatic ontology generation from a  | 11430 privacy policies from top Android apps | precision, contradiction counts, percentage of pol | The authors report high precision by design and accept reduced recall as the cost [p. 16]. |
| `chen2025llms` | empirical | prompt engineering (5 prompt designs, zero-shot/few-shot/CoT); LoRA fine-tuning; | OPP-115, GoPPC-150, CAPP-130, APPCP-100 | macro-average F1, micro-average F1, Fleiss kappa,  | — |
| `delalamo2022systematic` | survey | systematic mapping study; expert-developed annotation scheme for paper coding; s | 39 papers selected from 1097 candidate publication | paper counts by category | 39 papers analyzed from 1,097 found [p. 1]. |
| `goknil2024privacy` | systems | zero-shot, one-shot, few-shot prompting; chain-of-thought prompting; prompt temp | OPP-115 | F1 (per data-practice category, with confidence in | — |
| `harkousnodatepolisis` | systems | domain-specific word embeddings (privacy-centric language model); hierarchy of C | OPP-115, 130K unlabelled privacy policy corpus, Di | F1 (per category), icon assignment accuracy, top-k | Icon assignment at 88.4% [p. 9] lands inside the band of human expert inter-rater agreement |
| `wilson2016creation` | dataset | expert-developed annotation scheme; fine-grained manual annotation by trained la | OPP-115 | Fleiss kappa, segment-level category coverage, ann | 115 documents, 266,713 words [p. 4]. |

## Datasets across the corpus

- **OPP-115** — `chen2025llms`, `goknil2024privacy`, `harkousnodatepolisis`, `wilson2016creation`
- **11430 privacy policies from top Android apps** — `andownodatepolicylint`
- **130K unlabelled privacy policy corpus** — `harkousnodatepolisis`
- **39 papers selected from 1097 candidate publications** — `delalamo2022systematic`
- **APPCP-100** — `chen2025llms`
- **CAPP-130** — `chen2025llms`
- **Disconnect privacy icons ground truth** — `harkousnodatepolisis`
- **GoPPC-150** — `chen2025llms`
- **Twitter-sourced question set** — `harkousnodatepolisis`
