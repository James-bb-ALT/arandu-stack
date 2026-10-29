# Repository Operating Contract

This file applies to the entire repository.

## Product

Build and ship a minimum lovable local Brazilian Portuguese NLU for Home
Assistant. The product has two parts:

1. a passive Home Assistant add-on that turns one utterance plus one current,
   bounded catalog snapshot into zero or one typed plan; and
2. a companion integration that builds that snapshot, revalidates the result
   against live Home Assistant state and caller permissions, executes an
   authorized operation, and renders the answer.

The add-on never receives Home Assistant credentials and never executes Home
Assistant services. The integration is the sole execution boundary. No
external supervisor script is part of the product.

The active normative set is `AGENTS.md`,
`docs/clean-room/USER-DECISIONS.md`, `docs/mlp/PRODUCT.md`,
`docs/mlp/REQUIREMENTS.md`, and accepted ADRs dated 2026-09-12 or later.
Earlier phase queues, requirement matrices, tribunal reports, and P00-P16
artifacts are retained only as historical evidence and do not gate the MLP.
Newer explicit user constraints take precedence.

As of 2026-09-23, sessions, follow-ups, clarification, and music are in scope.
Spotify, Deezer, and other streaming services are content providers only.
Arandu never integrates directly with those providers and never treats them as
players or execution backends. All music search and playback must go through
Music Assistant; Home Assistant `media_player` entities are accepted for music
only when they are identified as Music Assistant players.

Resolve decisions in this order: newer explicit user constraints, this
contract, the newest accepted active ADR, the MLP requirements, official
public contracts, valid tests, then current implementation. At the same level
choose the safer, fail-closed, licensed, correct, deterministic, simple, and
reversible option.

## Hard Boundaries

- Use only free and open-source software, data, models, build tools, runtime
  tools, base images, and generated inputs.
- Software licenses must be OSI-approved. Data licenses must permit use,
  modification, redistribution, and commercial use.
- Do not use Amazon-specific or Amazon-internal material, including public
  AWS/Amazon projects.
- Do not inspect or use Sophia, `cicero-sophia`, another closed engine, a
  sibling directory, or a prior implementation as an input.
- Do not commit credentials, personal Home Assistant state, residential
  dumps, or user utterances.
- Runtime is offline except for the explicit local Home Assistant
  integration-to-add-on request and Home Assistant calls made by the
  integration.
- Project-authored PT-BR conformance text must be generated from the frozen
  deterministic MLP corpus specification, licensed Apache-2.0, and reported
  only as internal conformance data. Never use product output as gold data.
- Technical strings invented only to exercise code are `FIXTURE_TECNICA` and
  are excluded from linguistic metrics.

The user's operating system and system libraries are ambient host boundaries,
not distributable project inputs.

## MLP Scope

The MLP supports:

- `turn_on`, `turn_off`, `set_fan_percentage`, and `get_state`;
- `light`, `switch`, and `fan` effects;
- read-only `sensor` and `binary_sensor` state;
- one plan with up to four ordered operations, each over up to four exact
  entity or area clauses;
- coordinated PT-BR ellipsis such as `apaga a luz da sala e do quarto`;
- mixed effect chains such as `apague X e ligue Y`;
- deterministic PT-BR command forms and exact normalized aliases; and
- one-shot responses with explicit abstention or ambiguity.

One operation may contain multiple sorted targets, while plan operations
preserve spoken order. The integration must preflight the complete plan before
the first effect. Contradictory reuse of a target across operations abstains.
The remaining exclusions are timers, toggle, fuzzy matching, whole-home
broadcast, climate, covers outside Music Assistant playback, locks, scenes,
arbitrary service passthrough, and direct provider APIs. Unsupported or
ambiguous requests produce no executable plan; when the action is understood
but a required target or music slot is missing, the integration may ask a
bounded clarification question and continue the conversation once.

## Engineering Rules

- Preserve the original Unicode input value and use checked UTF-8 boundaries
  whenever byte spans cross a contract.
- Fail closed on ambiguity, unsupported behavior, malformed input, stale or
  changed catalog state, missing service, disabled or unexposed entities,
  denied caller permission, or transport failure.
- Keep interpretation, authorization, execution, and rendering separate.
- Emit deterministic JSON with stable ordering and no timestamps, paths,
  random identifiers, locale-dependent values, or unordered maps in semantic
  results.
- Bound request bytes, text length, catalog entries, aliases, operations,
  target clauses, resolved targets, fan percentage, connection time, and
  response bytes.
- New behavior needs tests; a bug fix needs a regression test.
- Keep dependencies small and pinned. Record each dependency's version,
  license, provenance, and purpose.
- Do not add empty crates, placeholder modules, custom cryptography, durable
  execution ledgers, or background state synchronization.

## Delivery Discipline

There is one active MLP baseline, not a phase tribunal. Work continues directly
until the standard gate passes and the package is usable. The gate is
`./tools/mlp-check` and covers formatting, linting, unit tests, corpus
conformance, integration tests, package structure, and a local service smoke
test.

Before release, obtain two read-only reviews of the same tested tree:

1. product correctness and fail-closed behavior; and
2. security, privacy, licensing, and package boundary.

Reviews use primary evidence and attempt counterexamples, but they do not
create candidate budgets, terminal guards, self-hashing transcripts, or
governance mutation suites. The executor owns integration and fixes material
findings directly. A review may be unavailable without blocking truthful
delivery if the complete gate passes and the limitation is reported.

## Workflow

1. Freeze the MLP contract and deterministic corpus before implementing the
   new engine.
2. Implement the smallest complete vertical slice.
3. Run and fix the standard gate.
4. Perform the two focused release reviews and fix material findings.
5. Rerun the standard gate, package the deliverable, and document exact
   supported behavior and limitations.

Use `apply_patch` for manual edits. Use `find` and `grep` when `rg` is
unavailable. Never claim a command passed unless it was executed.
