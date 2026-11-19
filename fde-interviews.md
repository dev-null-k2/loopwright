# Preparing for forward-deployed engineering interviews

A forward-deployed engineer (also: solutions engineer, applied AI engineer, deployed engineer) builds and ships
systems inside a customer's environment. The interview loop tests two things in the same work: can you build a
production AI system, and can you be trusted alone with a customer. This page maps each round of a typical loop
to what in this repository prepares you for it, and says plainly what it does not cover.

The rounds below are the ones candidates and preparation guides reported for 2026 loops at AI labs and
AI-native companies. Loops differ; ask your recruiter which rounds yours has.

## The loop, round by round

| Round | What it tests | Practise here |
|---|---|---|
| Recruiter and hiring-manager screens | Why this role and not product engineering; whether your past work looks like the job | [Why forward-deployed](interview-prep.md#why-fde) |
| Coding | Practical engineering: an API client, a data transformation, a small service. Clarify, get something running, adapt when requirements change | The [exercises](exercises.md): write the loop, a retry, an idempotent action, a gate, a grader, with a checker. Then [Write the agent loop, live](interview-prep.md#coding-loop) |
| System design | An LLM system in a real deployment: retrieval, agents, guardrails, evals, cost and latency | The system-design scenarios, [retrieval at scale](interview-prep.md#rag-at-scale), [a money action](interview-prep.md#high-impact-action), [conflicting sources](interview-prep.md#conflicting-sources) |
| Case study, or decomposition | A vague customer ask turned into a scoped first deployment. Often the round that decides the offer | [An assistant for clinicians](interview-prep.md#case-clinicians), [Automate our disputes](interview-prep.md#case-disputes) |
| Customer conversation, or role-play | Discovery questions, explaining a tradeoff in plain words, pushing back without losing the customer | [The first meeting](interview-prep.md#discovery-first-meeting), [The pilot is failing](interview-prep.md#customer-pushback), [The security review](interview-prep.md#security-review) |
| Integration | Systems you do not own: duplicates, ordering, legacy software, rate limits | [Webhooks and a CRM](interview-prep.md#webhooks-crm), [No API](interview-prep.md#legacy-no-api) |
| Take-home and project deep dive | A small working system, presented and defended decision by decision | [A week to build and present](interview-prep.md#take-home) |
| Behavioural | Ownership, ambiguity, a failure that was yours | [Why forward-deployed](interview-prep.md#why-fde): the four stories to prepare |

## The questions that come up most, and where each is answered

| Question | Where |
|---|---|
| When do you use a workflow, RAG, or an agent? | Lesson 18 runs both on the same task; card [Workflow or agent](glossary.md#workflow-vs-agent) |
| Prompting, RAG or fine-tuning? | Card [Prompting vs RAG vs fine-tuning](glossary.md#customisation-options); scenario [The pilot is failing](interview-prep.md#customer-pushback) |
| How do you evaluate an LLM system beyond "looks right"? | Lesson 24, the Evals tab, cards [Evals](glossary.md#evals), [Trajectory evals](glossary.md#trajectory-evals), [LLM as judge](glossary.md#llm-as-judge) |
| How do you stop hallucination? | Lesson 7 (answers with checked citations), card [Hallucination](glossary.md#hallucination) |
| How do you defend against prompt injection? | Lessons 10 and 11: the same attack with guardrails on and off |
| How do you let an agent take a risky action safely? | Lessons 5 and 9, card [Tool gate](glossary.md#tool-gate), scenario [A money action](interview-prep.md#high-impact-action) |
| What is your chunking strategy, and how do you justify it? | Cards [Chunking](glossary.md#chunking), [Hybrid search](glossary.md#hybrid-search), [Reranking](glossary.md#reranking); the retrieval table in the Evals tab is the justification: a number per ranker |
| How do you keep permissions when many people share one assistant? | Card [Multi-tenancy](glossary.md#multi-tenancy) (explained, not built here) |
| What happens to the customer's data? | Card [Data boundaries](glossary.md#data-boundaries) (explained, not built here) |
| How do you cut cost and latency? | Scenario "Cut latency and cost"; cards [Prompt caching](glossary.md#prompt-caching), [Semantic cache](glossary.md#semantic-cache), [Model gateway](glossary.md#model-gateway) |
| Walk me through an agent end to end. | This is the cockpit. Run a turn and narrate the board box by box, then do it without looking |

## A two-week plan

**Week one: be able to explain every box.**

1. Days 1 to 3: do all 25 lessons in order in the cockpit. After each, close it and say out loud what happened
   and why that mechanism exists.
2. Days 4 and 5: open the Lab and switch things off (memory, guardrails, retries). Predict what breaks before you
   send the message. The ablation table in the Evals tab shows the same thing as numbers.
3. Days 6 and 7: flashcards until the short answers are automatic. Then read `harness.py`, `loop.py` and
   `toolgate.py` top to bottom; the walk-through is in [architecture.md](architecture.md).

**Week two: practise the rounds.**

4. Days 8 and 9: one system-design scenario a day, spoken, timed at 35 minutes, then compare with the answer.
5. Days 10 and 11: the two case studies and the three customer scenarios. With a real model connected, use
   **Be interviewed on this** in the Learn tab: the agent plays the interviewer, pushes back, and grades you
   when you say "grade me".
6. Day 12: the [exercises](exercises.md). Do the loop, retry, limit and idempotency ones without hints, then write
   the loop again from memory in fifteen minutes and compare with `loop.py`.
7. Days 13 and 14: your four behavioural stories, written with numbers, each under two minutes. Then a small
   take-home of your own: pick a brief, change Loopwright to fit it (a new tool, three eval cases, one
   ablation), and present it to someone.

## What this does not cover

- **Algorithm puzzles.** Some loops still ask graph and tree problems. Practise those elsewhere.
- **Your own stories.** The behavioural scenario gives the shape; the content has to be yours.
- **Company specifics.** Each company's products, customers and pricing. Read their documentation and case
  studies.
- **A real customer.** Nothing replaces having sat with users. If you have not, the take-home practice above is
  the closest substitute, and say so honestly in the interview.
- **Classic distributed-systems design** beyond what an agent needs (queues, idempotency, retries, caching are
  here; consensus, storage engines and the like are not).
- **Model internals and training.** Fine-tuning is explained as a decision, not taught as a skill.
- The answers here were written by the project's author and have not been reviewed by people who run these
  interviews.
