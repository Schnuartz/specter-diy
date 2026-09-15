# Repo conventions for Claude Code sessions

## Commit identity

Commits made by Claude Code in this repo should be authored as the
project's own AI account, not as "Claude":

    git config user.name "Schnuartz AI"
    git config user.email "schnuartz.ai@gmail.com"

Do not append a `Co-Authored-By: Claude ...` or `Claude-Session: ...`
trailer to commit messages (`.claude/settings.json` sets
`includeCoAuthoredBy: false` for this).

Note: this only covers commit authorship and commit trailers. GitHub
comments, reviews, and review replies posted by Claude Code still carry
the standard Claude Code attribution footer — that's a fixed platform
policy, not something this file can turn off.
