# Development methodology

## Product and authority boundary

Atlas is both a working product and the primary reference workload for a
harness-engineered software-development environment. All implementation coding
for Atlas is performed through a harness-mediated agent workflow. The project
owner retains authority over product intent, requirements, architecture, risk,
and final acceptance; coding agents perform bounded implementation work through
the harness.

This is a development method, not a claim that model output is self-validating.
Human ownership and reproducible acceptance remain necessary even when agents
produce the implementation.

## Repository as engineering memory

The methodology treats the repository as durable engineering memory:

- domain rules become explicit contracts;
- architecture becomes machine-readable dependency boundaries;
- security and authority semantics become tests and audits;
- supported operator journeys become reproducible smoke environments; and
- applicable review findings become regression guards.

The goal is that a newly started agent, without the previous conversation, can
reconstruct the project context required for a bounded change and submit it to
reproducible acceptance checks.

## Public snapshot boundary

Atlas is the product and reference workload through which this methodology is
exercised and improved. The complete harness and its private execution records
are not part of the public snapshot. This repository exposes the resulting
architecture constraints, tests, audits, smoke scripts, and runnable
application instead.

Those artifacts are evidence about the checked-out source tree. They are not a
security certification, production readiness claim, formal proof of model
accuracy, or guarantee that every external Provider or deployment environment
will behave identically.

See [Verification](verification.md) for the public checks and
[Architecture and trust boundaries](architecture.md) for the runtime contracts
they protect.
