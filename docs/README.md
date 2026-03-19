# Docs Index

This folder is the repository-local knowledge base for agent-first work.

## How To Use This Directory
1. Start with product intent in `product/`.
2. Read architecture boundaries in `architecture/`.
3. Check quality expectations in `quality/`.
4. For multi-step work, use `plans/`.
5. Record new terms in `glossary.md` and new debt in `debt/tech-debt.md`.

## Structure
- [core-beliefs.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/core-beliefs.md): Operating principles for humans + agents
- [glossary.md](/home/rhuu/mygit/hypercheap-voiceAI/docs/glossary.md): Domain terms and definitions
- `architecture/`: System domains, layers, dependency direction, and flows
- `product/`: Product intent, workflows, and requirements
- `plans/`: Active and completed execution plans
- `quality/`: Quality and reliability expectations
- `references/`: Curated external/reference context
- `debt/`: Technical debt register
- `generated/`: Placeholder for generated reference artifacts

## Update Rules
- If code behavior changes, update relevant product and architecture docs in the same change.
- If design decisions change, reflect them in active/completed plans.
- If a term is introduced or overloaded, update `glossary.md`.
- Store task scope and implementation decisions in `docs/plans/`; do not depend on a root `TASK.md`.
