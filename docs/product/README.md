# Nautobot Day2 — Product Documentation

This is the product-level documentation set for **Nautobot Day2**, written
for a broader audience than the engineering docs in `docs/` (which remain
the authoritative source for implementation detail, exact file paths, and
operational runbooks). These pages are meant to be copied into Confluence
as a proper product space — one page per file below, in this order.

| Page | Covers |
|---|---|
| [`01-PRODUCT-OVERVIEW.md`](./01-PRODUCT-OVERVIEW.md) | What Nautobot Day2 is, who it's for, the problem it solves, and how it's packaged/deployed. Start here. |
| [`02-ARCHITECTURE.md`](./02-ARCHITECTURE.md) | System architecture at product altitude — the major subsystems, how they connect, and the three core flows (onboarding, day-2 sync, live troubleshooting). Links to `docs/02-COMPONENTS.md` / `docs/03-ARCHITECTURE.md` for engineering-level depth. |
| [`03-FEATURES.md`](./03-FEATURES.md) | The feature catalog — every capability, grouped by area, with a status (GA / Beta / Known Gap) and who uses it. |
| [`04-GLOSSARY-AND-CONCEPTS.md`](./04-GLOSSARY-AND-CONCEPTS.md) | Domain concepts a new reader needs before the other pages make sense — tenant/namespace model, shadow IP, VIP coverage, secrets groups, etc. |
| [`05-SECURITY-AND-COMPLIANCE.md`](./05-SECURITY-AND-COMPLIANCE.md) | Security posture as of this writing — what's enforced today, what's a known open gap, and priority to close each. Stakeholder-level summary of `docs/06-GAPS-AND-RECOMMENDATIONS.md`. |
| [`06-KNOWN-ISSUES-AND-RISKS.md`](./06-KNOWN-ISSUES-AND-RISKS.md) | Condensed, non-security operational risks and open questions — the rest of `docs/06-GAPS-AND-RECOMMENDATIONS.md`, in table form. |
| [`07-ROADMAP.md`](./07-ROADMAP.md) | Roadmap — the five-theme forward vision (complete system of record, command execution abstraction, chat/MCP inventory search & reporting, config change history & approval workflow, interactive add/edit/delete via MCP), plus a Now/Next/Later structure still to be filled in with sized, sequenced items. |

## How this set relates to the engineering docs

```
docs/
├── product/                 ← you are here (this page's folder) — Confluence-bound, stakeholder-facing
│   ├── 01-PRODUCT-OVERVIEW.md
│   ├── 02-ARCHITECTURE.md
│   ├── 03-FEATURES.md
│   ├── 04-GLOSSARY-AND-CONCEPTS.md
│   ├── 05-SECURITY-AND-COMPLIANCE.md
│   ├── 06-KNOWN-ISSUES-AND-RISKS.md
│   └── 07-ROADMAP.md
├── 00-WORKFLOW.md           ← engineering: branch/release process
├── 01-ONBOARDING-GUIDE.md   ← engineering: step-by-step for an engineer running onboarding
├── 02-COMPONENTS.md         ← engineering: what each component does
├── 03-ARCHITECTURE.md       ← engineering: detailed flow diagrams
├── 04-COMPONENT-PATHS.md    ← engineering: exact file paths, ports, env vars
├── 05-MONITORING.md         ← engineering: what to monitor, alert priorities
└── 06-GAPS-AND-RECOMMENDATIONS.md  ← engineering: the full, unabridged gap list (18 items)
```

The product pages summarize and reframe; they don't replace the
engineering docs. When the two ever disagree, the engineering docs and
the code are correct — these pages should be updated to match, not the
other way around.

## Publishing to Confluence

Each page is plain Markdown with standard tables and a few
[Mermaid](https://mermaid.js.org/) diagram code blocks. Confluence's
native Markdown import (Cloud: **Import** → **Markdown**, or paste
directly into a page and Confluence auto-converts headers/tables/code
blocks) handles everything except Mermaid rendering — either install the
Mermaid macro/app from the Atlassian Marketplace, or render each diagram
to an image first (e.g. via the `mermaid-cli` tool or the Mermaid Live
Editor) and drop the image in instead of the code block.

Suggested Confluence page hierarchy (mirrors the file order above):

```
Nautobot Day2 (space home)
├── Product Overview
├── Architecture
├── Features
├── Glossary & Key Concepts
├── Security & Compliance
├── Known Issues & Risks
└── Roadmap
```

## Keeping this current

This set reflects the codebase as of the `staging`/`main` merge that
included the shadow-IP/VIP tracking feature and the `onboarding-mcp`
conversational onboarding surface (the most recent major addition at time
of writing). When a feature is added, changed, or removed, update
`03-FEATURES.md` and, if it changes a core flow, `02-ARCHITECTURE.md` —
in the same PR as the code change, the same discipline `docs/06-GAPS-AND-RECOMMENDATIONS.md`
already expects of the engineering docs.
