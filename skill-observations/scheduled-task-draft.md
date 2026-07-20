Run the task-observer skill's comprehensive review in scheduled autonomous mode (user not present) for the whisperdesk project at C:\Claude\whisperdesk.

Procedure:
1. Read skill-observations/log.md. Archive entries already marked ACTIONED or DECLINED in a prior session to skill-observations/archive/log-<today>.md (keep the header and status key in the archive file; the active log keeps its header plus OPEN and ESCALATED entries). Never archive ESCALATED entries; they wait for the user.
2. If there are no OPEN observations, write today's date to skill-observations/last-review-date.txt and stop, reporting "no open observations". Do not re-evaluate ESCALATED entries.
3. For each OPEN observation, apply it per the established convention: integrate a compact rule cited as (wd#N) into the global CLAUDE.md content, placed in the most closely related existing section, matching the file's terse style, no em or en dashes in the rule text. Do NOT edit C:\Users\tito1\.claude\CLAUDE.md directly; the harness sensitive-file gate blocks headless writes to it. Instead, start from the wrapper's pre-run snapshot at skill-observations/claudemd-backup.md and write the complete updated file to skill-observations/claudemd-staged.md. The wrapper copies the staged file over the live CLAUDE.md after a clean exit, so treat a written staged file as an applied change.
4. Escalate instead of applying: new-skill candidates, anything that would remove or restructure existing CLAUDE.md content, observations whose Suggested Improvement flags its own uncertainty, and conflicting observations. Set each escalated observation's Status to `ESCALATED — <one-line reason> (<today>)` in log.md so later runs skip it.
5. Mark applied observations ACTIONED with a note naming the CLAUDE.md section and the review date. Do not edit any other field.
6. Write today's date to skill-observations/last-review-date.txt.
7. Write a short report of applied/escalated/archived items to skill-observations/last-scheduled-review-report.md. If you staged CLAUDE.md changes, include a summary of them: diff skill-observations/claudemd-staged.md against skill-observations/claudemd-backup.md.

Constraints: do not create new skills, do not delete observations, do not run any git command (the wrapper script commits the review artifacts), do not push.
