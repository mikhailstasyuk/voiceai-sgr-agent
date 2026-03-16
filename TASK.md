# Codex Repository Setup Prompt (Documentation-Only, Repository-Agnostic)

You are preparing this repository to operate in an **agent-first engineering environment using Codex**, following the practices described in **OpenAI’s “Harness engineering: leveraging Codex in an agent-first world.”**

Your task is to **create a documentation-only scaffolding** that allows Codex to reliably understand, navigate, plan, and implement work in the repository.

⚠️ Important constraints:

- **Do NOT create scripts, automation, CI pipelines, or tooling**
- **Do NOT modify build systems**
- **Do NOT add linters or enforcement mechanisms**
- Only produce **documentation, structure, and written conventions**
- All guidance must live **inside the repository**
- The goal is to make the repository **agent-legible and navigable for Codex**

Your output should be a **structured documentation system** that allows Codex to:

- understand architecture
- understand product intent
- locate relevant code
- create execution plans
- reason about changes
- update documentation when behavior changes
- escalate when human judgment is required

---

# Guiding Principles (From OpenAI Agent-First Engineering Practices)

Ensure the repository documentation reflects these core principles.

## 1. Humans steer, agents execute

In this workflow:

- Humans define intent, architecture, and constraints.
- Codex performs the implementation work.

The repository must therefore prioritize **clarity of intent and system maps** over exhaustive explanations.

---

## 2. The repository is the system of record

Codex primarily understands the system through what exists **inside the repository**.

Information that lives only in:

- chat threads
- Slack
- external documents
- people's heads

is effectively invisible.

Therefore:

- key architectural knowledge must live in the repo
- product behavior must be documented
- plans must be recorded
- terminology must be defined

---

## 3. AGENTS.md must be small and act as a map

Do **NOT** create a giant instruction file.

Instead create a **short AGENTS.md (~100 lines)** that functions as a **routing guide**.

AGENTS.md should:

- explain the repo’s agent-first workflow
- link to authoritative documents
- instruct Codex where to find:
  - architecture
  - product specs
  - plans
  - domain definitions
  - quality expectations
  - terminology

AGENTS.md is a **table of contents**, not a manual.

---

## 4. Context should be organized, not dumped

Codex performs best when information is **structured and indexed**.

Avoid long unstructured documents.

Prefer:

- indexes
- domain maps
- small topic-focused documents
- clear cross-references

---

## 5. Architecture must be explicit

Agent-driven systems work best when architecture is **clear and predictable**.

Documentation should describe:

- system domains
- logical layers
- dependency direction
- cross-cutting concerns
- where new code should live

The goal is **architectural legibility**, not theoretical perfection.

---

## 6. Boundaries matter more than micro-style

Codex performs best when the system has clear **boundaries**.

Documentation should describe:

- domain boundaries
- service boundaries
- module responsibilities
- dependency direction

Precise style conventions are less important than **clear structural rules**.

---

## 7. Data boundaries should be explicit

Agent-generated code must not rely on guessing data shapes.

Documentation should explain:

- where data enters the system
- where validation occurs
- expected schemas
- contract expectations

The goal is preventing "guessing data structures."

---

## 8. The system must be legible to agents

Codex should be able to understand:

- how the application works
- what components exist
- how the system behaves

Documentation should therefore include:

- architecture diagrams (conceptual)
- service descriptions
- request flow descriptions
- domain explanations
- system boundaries

---

## 9. Execution plans should live in the repository

Codex performs best when complex tasks have **structured execution plans**.

Plans should be:

- versioned
- documented
- updated during execution
- archived when complete

---

## 10. Human feedback should compound

Repeated review feedback should eventually become:

- documented rules
- architectural guidance
- conventions

Documentation should capture these insights so they compound over time.

---

## 11. Continuous cleanup is necessary

Agent-generated code can produce drift and inconsistency.

The documentation system should therefore support:

- documenting technical debt
- identifying outdated plans
- tracking architectural drift
- recording cleanup work

---

# Required Documentation Structure

Create a repository documentation structure similar to the following.

Adjust names if a better fit exists for the repository.
docs/
    README.md
    core-beliefs.md
    glossary.md

architecture/
    overview.md
    domains.md
    layers.md
    dependency-rules.md
    system-flows.md

product/
    overview.md
    user-workflows.md
    requirements.md

plans/
    README.md
    active/
    completed/

quality/
    quality-standards.md
    reliability-goals.md

references/
    external-context.md

debt/
    tech-debt.md