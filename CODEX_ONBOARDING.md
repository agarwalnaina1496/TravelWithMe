<!-- twm-codex-basekit: START -->
# Codex onboarding for backend

This repository is managed with TWM Codex Basekit version 0.1.0.

## Developer setup

1. Clone or update the `TWM-AI-Basekit` sibling repository.
2. From the Basekit repository, run:

   ```powershell
   .\setup.ps1 -Profile backend -RepositoryPath "<absolute-repository-path>" -Mode Update
   ```

3. Start a new Codex task from this repository root so Codex reloads `AGENTS.md`.
4. Invoke shared workflows explicitly with `$twm-discover`, `$twm-plan-linear`, `$twm-implement`, `$twm-review`, `$twm-publish`, or `$twm-onboard-repo` when needed.

Use `-DryRun` to preview changes and `-Mode SkillsOnly` to update only the shared skills. Do not manually edit content between Basekit-managed markers; place repository-maintained notes outside those markers.
<!-- twm-codex-basekit: END -->
