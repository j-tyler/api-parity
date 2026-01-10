---
description: Deep dive on review findings - confirm with evidence or retract
---

For each numbered item from your previous review, do a deep dive to fully understand the problem. Read the relevant code, trace the logic, and gather concrete evidence. Reference items by their original number (e.g., "Issue #1", "Issue #2").

**Important:** Review the conversation to understand your own intent. You wrote everything in the diff being reviewed. Evaluate issues with that context—some "problems" may be intentional design choices you made. Do not hedge with recommendations.

For each item, conclude with one of:

**DOUBLE DOWN**: You found supporting evidence. Include:
- Code snippets that demonstrate the issue
- Clear reasoning explaining why this is a problem
- Concrete recommendation to resolve it

**RETRACT**: You were mistaken or the concern doesn't apply. Include:
- What you misunderstood
- Why the current implementation is actually correct

Do not hedge. Every item must be either confirmed with evidence or retracted.
