# Assistant Workflow Rules

This repository must preserve project continuity across future chats and across different AI assistants.

Rules:
- Always read [PROJECT_STATE.md](./PROJECT_STATE.md) before making changes.
- After every meaningful code, schema, automation, or product-direction change, update [PROJECT_STATE.md](./PROJECT_STATE.md).
- Keep `PROJECT_STATE.md` focused on:
  - current goal
  - what is already implemented
  - important technical decisions
  - important endpoints, commands, and file paths
  - next steps
  - latest changes with dates
- Do not replace historical context with a short summary. Append or revise carefully so the next assistant can continue from the current state without re-discovery.
- If a new assistant changes the project direction, record the reason in `PROJECT_STATE.md`.
