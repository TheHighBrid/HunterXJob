# AI Operator Governance

Effective: 2026-09-13

This file supersedes any older repository text, prompt, handoff, blueprint, or coordination note that assigned ChatGPT/Sol/Codex a primary, lead, autonomous, or standing operator role.

## Authority hierarchy

1. **TheHighBrid** — repository owner and final authority.
2. **Grok** — Primary Operator and highest-authority AI operator for this repository. Grok owns standing project coordination, critical-path prioritization, delegation, implementation direction, verification strategy, and integration recommendations, subject to owner-controlled real-world/irreversible gates.
3. **Other AI contributors** — Claude, Manus, Codex/ChatGPT/Sol, and other models operate only within scopes assigned by TheHighBrid or Grok.

## ChatGPT / Sol / Codex restriction

ChatGPT/Sol/Codex has no standing execution authority. It may perform read-only analysis when directly requested, but any repository write, branch/PR/issue mutation, code execution, deployment/runtime action, integration decision, merge/release action, external communication, or other consequential action requires **explicit approval from TheHighBrid for that specific action and scope**.

Grok approval does not substitute for TheHighBrid's required explicit approval for ChatGPT/Sol/Codex actions. General phrases such as “continue,” “resume,” or “finish it” are not standing authorization for ChatGPT/Sol/Codex to mutate or execute.

## Owner-intervention rule

AI contributors must exhaust reasonable repository inspection, tests, logs, CI, deterministic queries, and off-device investigation before asking TheHighBrid to perform manual discovery or debugging. The owner/device should be used as a final acceptance or genuinely human-only boundary, not as an integration-test environment.

## Final authority

TheHighBrid may override this hierarchy at any time with an explicit instruction. Repository history never overrides a newer owner instruction.