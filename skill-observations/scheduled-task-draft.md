Run the task-observer skill's comprehensive review in scheduled autonomous mode (user not present) for the whisperdesk project at C:\Claude\whisperdesk.

Procedure:
1. Read skill-observations/log.md. Archive entries already marked ACTIONED or DECLINED in a prior session to skill-observations/archive/log-<today>.md (keep the header and status key in the archive file; the active log keeps its header plus OPEN and ESCALATED entries). Never archive ESCALATED entries; they wait for the user.
2. If there are no OPEN observations, write today's date to skill-observations/last-review-date.txt and stop, reporting "no open observations". Do not re-evaluate ESCALATED entries.
3. For each OPEN observation, apply it per the established convention: integrate a compact rule into the user's global CLAUDE.md at C:\Users\tito1\.claude\CLAUDE.md, placed in the most closely related existing section, cited as (wd#N). Match the file's terse style. No em or en dashes in CLAUDE.md content.
4. Escalate instead of applying: new-skill candidates, anything that would remove or restructure existing CLAUDE.md content, observations whose Suggested Improvement flags its own uncertainty, and conflicting observations. Set each escalated observation's Status to `ESCALATED — <one-line reason> (<today>)` in log.md so later runs skip it.
5. Mark applied observations ACTIONED with a note naming the CLAUDE.md section and the review date. Do not edit any other field.
6. Write today's date to skill-observations/last-review-date.txt.
7. Write a short report of applied/escalated/archived items to skill-observations/last-scheduled-review-report.md. Include a summary of every change made to CLAUDE.md this run: the wrapper script snapshotted the pre-run copy at skill-observations/claudemd-backup.md, diff against it.

Constraints: do not create new skills, do not delete observations, do not run any git command (the wrapper script commits the review artifacts), do not push.
