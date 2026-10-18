# Cloud job-search workflow

The existing checkout is already isolated; use it directly. Do not create a Git worktree unless the user explicitly requests one.

Read `.agents/skills/applypilot/SKILL.md` and its references. Keep the upstream package generic. Candidate facts, résumé files, application records, compensation and profile answers belong outside this checkout. In this prepared cloud machine, private data is under `/workspace/job-search-private`; this directory must never be staged or published. Its candidate profile is the source of truth and includes updates that supersede the uploaded résumé.

Use Precision mode: screen actual official job descriptions, then tailor supported experience. Third-party CLI installation is `/workspace/.venvs/applypilot`, controlled by `APPLYPILOT_DIR=/workspace/job-search-private/applypilot`. It is available for reviewed discovery/help/doctor only. Do not run the upstream auto-apply, CAPTCHA solver, permission-bypass launcher or any dry-run that claims a submission. No LLM API account is required for the stdlib companion discovery workflow.

User authorizes submissions only after all stated requirements are verified. Salary/visa unknowns need review. Do not repeat blanket submission permission once a role falls within that authorization; do stop for missing high-impact facts, login/2FA, human verification or site restrictions. Count only an actual employer confirmation.

The GitHub daily workflow produces job-discovery reports, not autonomous résumé tailoring, recruiter messages or applications. Do not claim it is active until a scheduler run has been observed. Do not claim this chat will send unattended daily updates; that requires a supported persistent agent and delivery mechanism. Keep employment-targeting configuration and reports private when deploying the companion.
