# Validation — project-delivery

## Verified on 2026-11-26

- `python3 -m unittest discover -s scripts -p 'test_*.py' -v`: **3/3 pass**
  - full local lifecycle through `released`;
  - adopt preserves `stage`, `last_good_commit`, `created_at` and backup metadata;
  - environment lock rejects a moved root.
- Batch `intake.json`: new project + doctor + backup — pass.
- Batch `verify.json` (`stage=review`): pass.
- Batch `release.json`: `ready-for-release` → `released` — pass.
- Batch `doctor.json`: pass with zero problems.
- `/home/zeon/.qwenpaw/venv/bin/skills-ref validate`: **Valid skill**.
- `qwenpaw skills test project-delivery`: **0 findings, safe=True**.
- `qwenpaw skills enable project-delivery --agent-id default`: **Enabled: yes**.
- `qwenpaw daemon reload-config`: success.

## External actions not performed

No GitHub repository creation, push, external upload, server deployment or cron installation was performed. Those remain explicit external gates requiring `go zeon`.

## Note on invocation

The QwenPaw CLI reports the skill enabled. The `Skill` tool registry in an already-running conversation may require a new session before the new name appears in its static skill list.
