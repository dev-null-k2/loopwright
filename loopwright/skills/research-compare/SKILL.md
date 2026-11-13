---
name: research-compare
description: Compare two ideas by researching each side separately with worker agents.
triggers: compare, versus, vs, difference between, contrast
---
When the user asks you to compare two things:

1. Call `delegate` once with one task per side. Make each task self-contained: the worker
   sees only the task text, not this conversation.
2. Workers return short summaries. Write the comparison from those summaries only.
3. Give each side one short paragraph, then one line starting "In short:" that says when
   you would choose which.
4. Keep the workers' citations.
