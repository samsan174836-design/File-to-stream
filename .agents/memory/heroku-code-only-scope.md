---
name: Heroku code-only scope
description: The user's scope constraint for this Heroku-deployed project.
---

**Rule:** The app is deployed on Heroku; make app code changes only and leave Heroku configuration, dependencies, and deployment setup untouched unless the user asks.

**Why:** The user said the app is already deployed and asked for code-only edits.

**How to apply:** For UI and behavior work, edit project code without changing deployment configuration or installing dependencies unless explicitly requested.