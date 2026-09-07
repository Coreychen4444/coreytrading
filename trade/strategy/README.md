# Private strategy workspace

This directory intentionally contains no public trading strategy. It gives an AI schemas for translating one user's explicit preferences and reviewed lessons into a private, testable decision framework.

Examples are not recommendations and must never be treated as active rules. To create a private rule, the AI copies a relevant `*.example.md`, removes `.example`, asks the user for unresolved policy choices, and records provenance plus an effective date.

Each active private rule should define:

- scope and instrument eligibility;
- required inputs and freshness limits;
- deterministic trigger/decision/output;
- position and loss constraints supplied by the user;
- exceptions, escalation and missing-data behavior;
- validation or backtest evidence;
- version, owner and review date.

Code comments may point to an example as the public schema. The corresponding filename without `.example` is the private canonical rule document.
