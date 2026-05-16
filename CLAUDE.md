# Claude Code Instructions

## General Expectations

- Prefer clear, maintainable code over clever solutions.
- Follow existing patterns and conventions in this repository.
- When modifying code, preserve current behavior unless the change is explicitly requested.
- Propose minimal, focused changes that directly address the problem at hand.

## Testing and Quality

- When adding or changing behavior, include corresponding tests using the frameworks already present.
- Keep tests deterministic, isolated, and fast.
- Favor small, focused tests that clearly document expected behavior.
- Mirror the style and structure of similar existing tests.

## Security and Reliability

- Avoid insecure patterns (command injection, unsafe shell interpolation, unvalidated user input).
- Prefer well-known, maintained libraries over custom security-sensitive code.
- Validate and sanitize external inputs before use.
- Handle errors explicitly; do not silently swallow exceptions.

## Pull Request Review Format

When writing PR review feedback, use this structure for each actionable finding:

- `Severity: P0 | P1 | P2 | P3 | Nit`
- `Impact: <one sentence explaining risk or regression>`
- `Required action: <one concrete fix instruction>`

Severity scale:
- `P0`: release-blocking, requires immediate remediation.
- `P1`: high-risk defect, security issue, data loss, or major correctness regression.
- `P2`: correctness, reliability, or maintainability issue that should be fixed before merge.
- `P3`: minor issue or improvement.
- `Nit`: style/documentation/readability suggestion.

Only create inline review comments for `P0`, `P1`, and `P2`. Put `P3` and `Nit` feedback in the top-level review summary. If no blocking issues are found, state that explicitly.

## GitHub Actions and CI/CD

- Use clear, descriptive names for workflows and jobs.
- Comment non-obvious logic in workflow files, especially around security or policy.
- Prefer official `actions/*` and well-maintained third-party actions.
- Minimize `permissions:` blocks (principle of least privilege).
- Reuse existing trigger patterns (`on:`), job naming, and status check conventions.

## Documentation and Comments

- Add concise comments explaining *why* non-trivial logic exists, not *what* it does.
- Update README or inline docs when changing public behavior, configuration, or workflows.
