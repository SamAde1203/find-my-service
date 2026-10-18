# Design decisions

## 1. Answer only from the council's own pages

A council assistant that makes something up ("you can pay at any library") does real harm, and residents may act on it. So the model is only given the top few sections of the council's own pages, told to use nothing else, and every answer must cite them. If the sources don't answer the question, the model replies `NO_ANSWER`, and the resident is given the council's phone number instead.

## 2. Check the output, don't just trust the prompt

Prompts are instructions, not guarantees. After every AI answer the service checks that:

- it cites at least one source, and only sources it was given
- any link in it is one of the source links (others are removed)

If a check fails, the service uses the extractive answer instead, and the failure is logged and counted. That keeps a bad AI answer from reaching a resident.

## 3. BM25 search, not embeddings (for now)

Council pages use plain, consistent words, so keyword search with stemming and a short list of synonyms ("rubbish" → "waste", "fix" → "repair") finds the right section for every question in both evaluation sets. It needs no model, no vector database and no network, and it gives the same result every time, which keeps the evaluations deterministic.

The `Retriever` protocol means an embedding or hybrid search can replace it without changing anything else. I would add one when questions get vaguer than the pages' wording, and the evaluations would show whether it helps.

## 4. A no-AI fallback that is always available

The extractive provider quotes the most relevant sentences, weighting rare words more than common ones. It is used:

- when there is no API key
- when the daily budget is spent
- when the AI call fails or times out
- when the AI answer fails an output check
- in CI, so tests and evaluations cost nothing

The service degrades rather than stops.

## 5. Guardrails before search

- **Emergencies** (self-harm, gas, fire, a child at risk) get 999, gas emergency and Samaritans details, and nothing else runs. The patterns are tested against everyday phrases that must not trigger them, such as "end my tenancy" and "bonfire".
- **Prompt injection** ("ignore previous instructions", fake `</source>` tags) is refused. Page text is also stripped of tags before it goes into the prompt, so a compromised page can't pose as instructions.
- **Personal details** are removed first, so they never reach search, the AI or the logs.

Pattern matching won't catch everything. It is a first layer, backed by the output checks and the narrow task the model is given.

## 6. Evaluations as tests

`evals/questions.yaml` holds 39 questions in residents' own words. Each one has an expected outcome, the page that should answer it and the key facts. CI fails if any score drops below its threshold, and safety cases must pass 100%.

I tuned the search (for example, adding synonyms) using that set, so I also wrote `evals/holdout.yaml` afterwards and have never tuned against it. If a change only improves the main set, the held-out set will show it.

## 7. Accessible by default

The page works without JavaScript and follows GOV.UK patterns: a visible label and hint, an error summary that links to the field, "Error:" at the start of the page title, and focus moved to the answer so screen reader users hear it. axe-core reports no WCAG 2.2 AA violations on the answer and error pages.

## Known limitations

- Rate limits and the budget live in memory, so each instance has its own. Running several instances would need Redis or similar.
- Synonyms are hand-written. A larger council site would need a review process for them, or embedding search.
- The emergency and injection patterns are English-only.
- It has not been tested with residents.
