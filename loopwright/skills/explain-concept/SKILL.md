---
name: explain-concept
description: Explain an agent-design term the way a good interviewer would want to hear it.
triggers: what is, what's a, what are, explain, how does, how do, why does, why do, define, walk me through
---
When the user asks about a concept in agent design:

1. Call `search_knowledge` with the term. Answer from the passages, not from memory.
2. Structure the answer in this order, briefly:
   - what it is, in one plain sentence
   - why it exists: the failure you get without it
   - the tradeoff: what it costs, and when you would not use it
   - where it lives in this codebase, as a file path
3. Cite each passage you used as [handbook: Term].
4. If the handbook has nothing relevant, say so. Do not improvise a definition.
