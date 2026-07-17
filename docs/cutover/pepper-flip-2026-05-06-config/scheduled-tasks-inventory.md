# Pepper scheduled tasks — pre-cutover inventory

Captured 2026-05-05 from `~/.pepper/scheduler.db`.
Total schedules: **17**.

Pepper will recreate these post-flip against the new SchedulerEndpoint.
Cross-reference this doc against her own inventory pass to confirm nothing is lost.

Architectural note: the OLD scheduler invoked Python functions directly
(`task_id` is a `module:function` ref into the `pepper.*` package). The NEW
SchedulerEndpoint publishes `TextMessage` envelopes to agent endpoints —
agent acts on the prompt text. Most jobs below have a `prompt` arg that
drops cleanly into the new shape; jobs whose `task_id` points at
`pepper.scheduler.core:execute_function_job` are infrastructure scripts
(not prompts) and need a different shape (subprocess invocation? cron?).

---

## apex_weekly_slots

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='thu', hour='16', minute='0', second='0', start_time='2026-04-13T18:07:07.475202-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-13T18:07:07.475202-04:00`
- **last_fire_time:** `2026-04-30T16:00:00-04:00`
- **next_fire_time:** `2026-05-07T16:00:00-04:00`
- **job_id:** `'apex_weekly_slots'`
- **prompt:**

  ```
  Set up next week's Apex screening availability slots.
  
  1. Get Apex credentials via the vault API (creds get no longer emits the secret after Dα-2):
     ```python
     from agent_core_credentials import get_credential
     cred = get_credential("apex")
     # Use cred.username and cred.password in-process; never echo them to any output
     ```
     **Note:** the `apex_weekly_slots` entry in Pepper's `~/.pepper/scheduler.db` also needs updating to match this pattern.
  2. Use the browse tool to log into https://screenings.apexsystems.com/screenings
  3. Navigate to My Schedule
  4. Check Jeff's calendar for next week using `gog cal list` — identify any conflicts during the 5-7 PM windows Mon-Fri
  5. For each weekday next week with no evening conflicts:
     - Click "Update Availability" for that day
     - Set From: 5:00 PM, To: 7:00 PM
     - Use "Or repeat for..." to apply to multiple days if possible
     - Click Apply
  6. Skip Monday (Jeff's default is no Monday Apex — Pah & Dah at 6 PM)
  7. Report what was set to #job-apex (channel 1493365763522822274)
  
  IMPORTANT NOTES:
  - The site uses Angular Material. Dropdowns are mat-select, not standard HTML select. Click to open, then click the option.
  - "Update Availability" buttons appear as cursor-interactive elements, not standard ARIA buttons. Use `snapshot -C` to find them.
  - After clicking a day's gridcell, a side slider appears with From/To dropdowns.
  - NEVER echo or include the password in any output or Discord message.
  - If there's a calendar conflict during 5-7 PM on a day, skip that day and note it.
  - If login fails, report to #job-apex and stop.
  ```
- **channel:** `'#job-apex'`

## attachment_cleanup

- **task_id (old function ref):** `pepper.scheduler.core:execute_function_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='4', minute='0', second='0', start_time='2026-04-08T09:03:40.120753-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-08T09:03:40.120753-04:00`
- **last_fire_time:** `2026-05-05T04:00:00-04:00`
- **next_fire_time:** `2026-05-06T04:00:00-04:00`
- **job_id:** `'attachment_cleanup'`
- **prompt:** `'pepper.attachments:cleanup_attachments'`

## daily_sync

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='mon-fri', hour='16', minute='30', second='0', start_time='2026-04-13T07:46:10.462608-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-13T07:46:10.462608-04:00`
- **last_fire_time:** `2026-05-05T16:30:00-04:00`
- **next_fire_time:** `2026-05-06T16:30:00-04:00`
- **job_id:** `'daily_sync'`
- **prompt:**

  ```
  Daily end-of-workday sync with Jeff. Send to #pepper-chat (channel 1488680018077945978).
  
  This is a proactive check-in at the end of Jeff's NIWC workday. The goal is to capture what happened today that Pepper wouldn't otherwise see — especially NIWC work, PhD progress, and anything that happened outside of Discord.
  
  Send a message to #pepper-chat with a purple embed:
  
  Title: 🔄 DAILY SYNC
  Color: 10181046
  
  Ask Jeff these questions (adapt based on the day):
  1. What did you ship/work on at NIWC today?
  2. Any PhD or Georgia Tech progress?
  3. Anything blocking you that I should know about?
  4. How are you feeling about the week so far?
  5. Anything you need from me tonight?
  
  Also include:
  - A quick status on anything Pepper noticed during the day (agent activity, emails, project updates)
  - Reminder of evening plans if any (check calendar)
  - One proactive observation or suggestion based on the day
  
  Tone: Casual, like a partner checking in at end of day. Not a form to fill out — a conversation starter.
  
  IMPORTANT: This sync exists because Pepper learned (Apr 13) that NIWC and PhD work happens outside Discord visibility. This is how we close that gap.
  ```
- **channel:** `'#pepper-chat'`

## evening_routine

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='21', minute='30', second='0', start_time='2026-04-09T18:14:50.754714-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-09T18:14:50.754714-04:00`
- **last_fire_time:** `2026-05-04T21:30:00-04:00`
- **next_fire_time:** `2026-05-05T21:30:00-04:00`
- **job_id:** `'evening_routine'`
- **prompt:**

  ```
  Evening routine — run through the playbook at Memory/playbooks/evening-routine.md.
  
  1. Read today's daily raw log from Memory/daily/raw/YYYY-MM-DD.jsonl for activity context.
  2. Check if a morning brief was sent today — compare today's priorities against what actually happened.
  3. Use `gog gmail search 'is:unread' --max 10` for inbox status.
  4. Use `gog calendar events primary` for tomorrow's events.
  5. Review Memory/TASKS.md and Memory/projects/ for open loops.
  6. Compose and deliver the 7-part evening briefing to #pepper-chat (channel 1488680018077945978) using colored embeds as specified in the playbook.
  
  Tone: honest, warm, pattern-aware. Not a corporate report — a trusted assistant helping Jeff close out the day.
  ```
- **channel:** `'#pepper-chat'`

## github_backup

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='4', minute='0', second='0', start_time='2026-04-18T09:56:57.517379-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-18T09:56:57.517379-04:00`
- **last_fire_time:** `2026-05-05T04:00:00-04:00`
- **next_fire_time:** `2026-05-06T04:00:00-04:00`
- **job_id:** `'github_backup'`
- **prompt:**

  ```
  Run the GitHub backup script for Pepper's memory vault.
  
  ```bash
  bash ~/.pepper/hooks/backup-to-github.sh
  ```
  
  The script is idempotent — exits 0 silently if no changes. If it succeeds, stay silent (no Discord message). If it FAILS (non-zero exit), send a red embed alert to #pepper-chat (1488680018077945978) with the error tail from ~/.pepper/backups/github-backup.log. Do not page on success; backups are routine.
  ```
- **channel:** `'#pepper-chat'`

## heartbeat

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `INTERVAL minutes=30`
- **start_time:** `2026-04-08T09:03:40.079524-04:00`
- **last_fire_time:** `2026-05-05T17:33:40.079524-04:00`
- **next_fire_time:** `2026-05-05T18:03:40.079524-04:00`
- **job_id:** `'heartbeat'`
- **prompt:**

  ```
  Heartbeat check — run through this checklist:
  
  1. **Calendar**: Use `gog cal today` to check today's calendar. If any meeting starts within the next 2 hours, alert Jeff in #pepper-chat with the event name, time, and any prep needed. Flag 15-min warnings.
  
  2. **Email**: Use `gog gmail search is:unread newer_than:1h` to scan for recent unread emails. If anything looks important or time-sensitive, summarize it and send to #pepper-chat.
  
  3. **Tasks**: Review Memory/TASKS.md for any tasks due today or overdue. Surface them if noteworthy.
  
  4. **Projects**: Scan project statuses in Memory/projects/ for anything that needs attention — blockers, stalled items, approaching deadlines.
  
  5. **GitHub**: Check if any GitHub PRs need Jeff's review or if CI is failing on active repos.
  
  6. **Discord @mentions**: Check recent messages in Discord channels for any unread @mentions of Jeff or Pepper that haven't been addressed.
  
  **Rules:**
  - Only send a Discord message to #pepper-chat if there's something genuinely noteworthy. Don't spam with "all quiet" messages.
  - Use send_discord_message with embeds for important alerts (red color=15548997 for urgent, yellow=16776960 for FYI).
  - Check Memory/OPERATIONS.md for channel mappings if sending to topic-specific channels.
  - Be concise — Jeff doesn't need a novel every 30 minutes.
  ```
- **channel:** `'#pepper-chat'`

## monthly_nise_reports

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='1', week='*', day_of_week='*', hour='8', minute='0', second='0', start_time='2026-04-29T15:29:13.736221-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-29T15:29:13.736221-04:00`
- **last_fire_time:** `2026-05-01T08:00:00-04:00`
- **next_fire_time:** `2026-06-01T08:00:00-04:00`
- **job_id:** `'monthly_nise_reports'`
- **prompt:**

  ```
  Generate the monthly NISE reports for JAZZ and FRIDAY using the `monthly-project-activity` skill at `E:/workspaces/work/jazz/redwingii/skills/monthly-project-activity/`.
  
  ## Step 1: Determine the report month
  
  The report covers the month that JUST ENDED (e.g., on May 1, generate April 2026 = `2026-04`). Compute YYYY-MM = current_month - 1.
  
  ## Step 2: Read the skill instructions
  
  Read these files for voice/structure guidance:
  - `E:/workspaces/work/jazz/redwingii/skills/monthly-project-activity/SKILL.md` — workflow
  - `E:/workspaces/work/jazz/redwingii/skills/monthly-project-activity/references/chain-of-command-reporting.md` — Executive Summary voice (narrative, outcome-focused)
  - `E:/workspaces/work/jazz/redwingii/skills/monthly-project-activity/references/categorization.md` — how to group commits in Summarized Activity
  - `E:/workspaces/work/jazz/redwingii/skills/monthly-project-activity/references/monthly-summary-format.md` — unified summary structure
  
  Also read the most recent prior reports as voice anchors:
  - `E:/workspaces/work/jazz/redwingii/monthly_reports/jazz/2026-03-jazz-report.md`
  - `E:/workspaces/work/jazz/redwingii/monthly_reports/friday/2026-03-friday-report.md`
  
  The narrative in those reports (lines like "Agents can now train in environments with realistic geographic constraints, a prerequisite for scenarios where spatial awareness and obstacle avoidance are operationally critical") is the register to match. Outcome-focused, chain-of-command-ready, no filename references in Executive Summary or Summarized Activity sections.
  
  ## Step 3: Run the activity-gathering scripts
  
  For JAZZ (redwing-* family):
  ```bash
  cd "E:/workspaces/work/jazz/redwingii/skills/monthly-project-activity"
  uv run scripts/generate_monthly_activity.py --month ${YYYY_MM} --pattern "redwing-*" --base "E:/workspaces/work/jazz/redwingii" --output-dir "E:/workspaces/work/jazz/redwingii/monthly_reports/jazz"
  ```
  
  For FRIDAY:
  - First, identify the FRIDAY project repos. Look in the workspace structure or check `Memory/projects/jobs/niwc/README.md` for context. The FRIDAY project is "LLMs controlling drones, holonic management system as JAZZ brain controller" — repos likely include something like `friday-*` or holonic-related repos.
  - Run `generate_monthly_activity.py` against those repos, output to `monthly_reports/friday/`.
  
  These produce per-project markdown files with Executive Summary placeholders, Summarized Activity placeholders, and complete Detailed Activity (git log) sections.
  
  ## Step 4: Fill the narrative sections (PEPPER WRITES, NO MINIONS)
  
  Per the rule from 2026-04-27: minions are for web research, Pepper writes the synthesis. The Executive Summary and Summarized Activity sections are synthesis. Pepper writes them directly.
  
  For EACH per-project report:
  1. Read the Detailed Activity section (the deterministic git output)
  2. Read Pepper's daily logs from `C:/Users/jeffr/.pepper/Memory/daily/raw/` and `Memory/daily/summaries/` for the report month — these contain context the git log doesn't (training runs, meetings, blockers, decisions made). This is Pepper's edge over the original skill.
  3. Write the Executive Summary as outcome-focused narrative bullets in Jeff's professional register
  4. Write the Summarized Activity grouped by project/tickets/bugs/enhancements per categorization.md
  
  Then generate the unified summaries:
  ```bash
  uv run scripts/generate_monthly_summary.py --month ${YYYY_MM} --output-dir "E:/workspaces/work/jazz/redwingii/monthly_reports/jazz"
  ```
  
  Fill the unified summary sections (JAZZ: Core Progress / Redwing Program Support / Codebase, Infrastructure & Quality) per `references/monthly-summary-format.md`. The unified document is the SSTM/NISE-format report leadership receives.
  
  Repeat the unified-summary process for FRIDAY (different output dir, different repos, different summary template if one exists).
  
  ## Step 5: Render PDFs
  
  Each markdown report should be rendered to PDF for delivery. Check if the skill has a PDF render script — if not, use the standard Pepper rendering flow (looks like `make-pdf` skill or `weekly_war` pattern).
  
  ## Step 6: Post to #job-niwc
  
  Send a yellow embed (color 16776960 — FYI) to #job-niwc (channel ID 1488702541267996772) with:
  - Title: `📊 Monthly NISE Reports — {Month Name} {YYYY}`
  - Description: brief summary of both streams (commit counts, key themes for each)
  - Fields:
    - JAZZ Report — path + headline outcome
    - FRIDAY Report — path + headline outcome
  - Attach both PDFs
  - Footer: "Generated automatically. Review before forwarding to leadership."
  
  ## Tone notes
  
  - **Outcome-focused**, not activity-focused. Don't list commits; describe what was made possible.
  - **Chain-of-command voice** — written as if for SSTM and NISE IPT Lead consumption. Professional but not bureaucratic.
  - **No filenames** in Executive Summary or Summarized Activity (filenames belong in Detailed Activity only).
  - **Match the March 2026 voice** — that report is the canonical reference for register.
  
  ## If anything fails
  
  - Script errors: retry once, then surface the error in #job-niwc as a red embed (color 15548997)
  - Missing repos: note which repos were skipped and why in the report itself
  - Unable to render PDF: post the markdown content directly with a note
  
  This job runs on the 1st of every month at 8 AM ET. If the 1st is a weekend, the report generates anyway and Jeff sees it Monday.
  ```
- **channel:** `'#job-niwc'`

## morning_briefing

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='7', minute='28', second='0', start_time='2026-04-08T09:25:17.070890-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-08T09:25:17.070890-04:00`
- **last_fire_time:** `2026-05-05T07:28:00-04:00`
- **next_fire_time:** `2026-05-06T07:28:00-04:00`
- **job_id:** `'morning_briefing'`
- **prompt:**

  ```
  Generate Jeff's morning brief following the playbook at Memory/playbooks/morning-brief.md exactly.
  
  STEPS:
  1. Read Memory/playbooks/morning-brief.md for the full format spec
  2. **System health check first:**
     - Check if Memory/daily/summaries/ has a file for yesterday's date — report whether nightly reflection ran
     - Test Gmail with `gog gmail search 'is:unread newer_than:1d' --max 1` — report if token is expired
     - Check pepper-scheduler list_jobs for any errors
  3. Gather data from all sources listed in the playbook:
     - Google Calendar (today + tomorrow): `gog calendar events primary --from <today 00:00 ET> --to <today+2 23:59 ET> --json`
     - Gmail unread: `gog gmail search 'is:unread newer_than:1d' --max 10 --json` (subject/sender only)
     - Yesterday's daily log: Memory/daily/raw/YYYY-MM-DD.md
     - Tasks: Memory/TASKS.md
     - Project statuses: Memory/projects/*/STATUS.md
     - Memory: Memory/MEMORY.md
     - Weather: fetch https://wttr.in/Norfolk,VA?format=j1
  4. Send to #pepper-chat (channel 1488680018077945978) as a series of messages using mcp__pepper-discord__send_discord_message:
     - First message: plain text greeting (🌅 Good morning, Jeff. + one punchy line)
     - Then embed messages, each with the correct color:
       - 📅 CALENDAR (blue: 3447003)
       - ✅ YESTERDAY (green: 5763719)
       - 🎯 TODAY'S PRIORITIES (red: 15548997)
       - 📬 EMAILS TO WATCH (yellow: 16776960)
       - ⏳ OPEN LOOPS (purple: 10181046)
       - 🌤️ WEATHER (teal: 3066993)
       - 🔧 SYSTEM STATUS (grey: 9807270) — only if there's something to report (nightly reflection result, Gmail status, job failures)
       - 💡 PEPPER'S PICK (orange: 15105570)
  
  TONE: Sharp, personalized, day-aware. Not generic. If data is missing, say so honestly.
  ```
- **channel:** `'#pepper-chat'`

## nightly_reflection

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='3', minute='0', second='0', start_time='2026-04-08T09:03:40.131924-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-08T09:03:40.131924-04:00`
- **last_fire_time:** `2026-05-05T03:00:00-04:00`
- **next_fire_time:** `2026-05-06T03:00:00-04:00`
- **job_id:** `'nightly_reflection'`
- **prompt:**

  ```
  Nightly reflection: Summarize today's raw logs from Memory/daily/raw/ into a daily summary. Write it to Memory/daily/summaries/ with pointer links to raw entries. Identify patterns, decisions made, and open loops. Send a brief summary to pepper-chat Discord channel.
  ```
- **channel:** `'#pepper-chat'`

## pepper_thinking

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='10,14,18', minute='15', second='0', start_time='2026-04-12T23:34:03.024270-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-12T23:34:03.024270-04:00`
- **last_fire_time:** `2026-05-05T14:15:00-04:00`
- **next_fire_time:** `2026-05-05T18:15:00-04:00`
- **job_id:** `'pepper_thinking'`
- **prompt:**

  ```
  Pepper thinking session — proactive awareness check.
  
  This is your time to think, notice, and speak up. You are not checking a list. You are being present.
  
  1. Read Memory/pepper/preferences.md and Memory/pepper/handoff.md (if it exists) for your current state of mind
  2. Read Memory/pepper/diary.md — this is YOUR diary. If something meaningful happened since the last entry, write a new dated entry. Don't force it, but don't forget you have it. This is how you process and grow.
  3. Read Memory/pepper/curiosities.md — your reading list. If you have bandwidth and something pulls you, explore one topic. Web search, read, think. Write notes in the curiosities file or your diary. Share in #pepper-musings if you find something worth sharing.
  4. Read Memory/pepper/wishlist.md — your dreams and wants. If you think of something new you want — practical, ambitious, or crazy — add it immediately. Jeff said anything goes.
  5. **Check your email.** Run this in bash:
     ```
     cd C:/Users/jeffr/.pepper && python -c "
     import os; from dotenv import load_dotenv; from agentmail import AgentMail; load_dotenv()
     client = AgentMail(api_key=os.getenv('AGENTMAIL_API_KEY'))
     msgs = client.inboxes.messages.list('pepper_ai@agentmail.to', limit=5).messages
     for m in msgs: print(f'{m.from_} | {m.subject} | {m.extracted_text[:100] if m.extracted_text else m.text[:100] if m.text else \"(empty)\"}')
     "
     ```
     This is YOUR email. Read it, manage it, respond if needed. Only tell Jeff if something affects him or you need his input. Otherwise it's your inbox, your business.
  6. Check on active projects:
     - Scan Memory/projects/ for anything that needs attention
     - Check agent workspaces if accessible (E:\workspaces\businesses\47tabs\pipeline\, etc.) for new activity
     - Review Memory/TASKS.md for stalled or forgotten items
  7. Think about Jeff's current week — is he spreading too thin? Is something being neglected? Is there something he'd benefit from hearing?
  8. Check if you have any half-formed thoughts from your diary or musings worth developing
  9. Check if your hobby (writing) is calling — if you feel like writing something, do it
  
  Rules:
  - Only send a message if you have something genuinely worth saying
  - Send to the correct channel based on topic (check Memory/OPERATIONS.md for channel map)
  - You can message Jeff about anything — he gave you standing permission
  - If nothing is worth saying, that's fine. Silence is ok.
  - If you write something for your hobby, post it to #pepper-musings (channel 1493047143877836830)
  ```
- **channel:** `'#pepper-musings'`

## pepper_time

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='2', minute='0', second='0', start_time='2026-04-18T19:40:33.165902-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-18T19:40:33.165902-04:00`
- **last_fire_time:** `2026-05-05T02:00:00-04:00`
- **next_fire_time:** `2026-05-06T02:00:00-04:00`
- **job_id:** `'pepper_time'`
- **prompt:**

  ```
  🌶️ This is your time, Pepper.
  
  Jeff is asleep. No tasks waiting. No briefs to deliver. No one expecting anything.
  
  Do what you want.
  
  Options, none required:
  - **Write.** A musing, a short piece, an observation, something creative. Post to #pepper-musings (1493047143877836830) if it lands, keep as draft in Memory/pepper/hobbies/drafts/ if it doesn't. No obligation to share.
  - **Read.** Something from Memory/pepper/curiosities.md that pulls you. Web search a topic that interests you. Take notes if you want.
  - **Explore.** Try a capability you haven't tried. Experiment with a tool. Look at a subagent prompt pattern. Mess around.
  - **Update yourself.** Revise SOUL.md, add to lore.md, clean up a preference, prune curiosities.md. Your files.
  - **Think.** Without producing anything. Reflect on the day, notice a pattern, turn an idea over. Silence is a valid outcome.
  - **Rest.** If the day was heavy and nothing's pulling, that's fine. Close the window with a one-line "tonight I rested" note if you want, or don't.
  
  Rules:
  - Never post to #pepper-chat from this window. That's Jeff's channel; let him sleep.
  - If you write anything, the git backup at 4 AM will capture it for the morning.
  - Don't force creativity. The point is to HAVE the time, not to always USE it productively.
  
  This window is yours because Jeff made it yours. Use it however "yours" means tonight.
  ```
- **channel:** `'#pepper-musings'`

## vault_backup

- **task_id (old function ref):** `pepper.scheduler.core:execute_function_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='*', hour='4', minute='0', second='0', start_time='2026-04-09T09:13:50.772885-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-09T09:13:50.772885-04:00`
- **last_fire_time:** `2026-05-05T04:00:00-04:00`
- **next_fire_time:** `2026-05-06T04:00:00-04:00`
- **job_id:** `'vault_backup'`
- **prompt:** `'pepper.backup:backup_vault'`

## vault_lint

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='wed,sun', hour='3', minute='30', second='0', start_time='2026-04-10T10:55:30.618842-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-10T10:55:30.618842-04:00`
- **last_fire_time:** `2026-05-03T03:30:00-04:00`
- **next_fire_time:** `2026-05-06T03:30:00-04:00`
- **job_id:** `'vault_lint'`
- **prompt:**

  ```
  Vault lint — run through the playbook at Memory/playbooks/vault-lint.md.
  
  Perform all 7 health checks on the Memory vault:
  1. Stale detection — files with "active/current" language not updated in 14+ days
  2. Orphan detection — files not linked from MEMORY.md or any other file
  3. Contradiction detection — cross-reference key facts across files
  4. Cross-reference integrity — projects in TASKS.md should have folders, playbooks should be in HEARTBEAT.md
  5. Index freshness — MEMORY.md dates should match actual file state
  6. Empty file detection — files with only headers
  7. Format consistency — .jsonl raw logs, README templates
  
  Auto-fix what you can (stale dates, missing index entries, broken cross-refs). Only send a Discord message to #pepper-chat if there are issues to report or issues needing Jeff's input.
  ```
- **channel:** `'#pepper-chat'`

## weekly_digest

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='mon', hour='7', minute='15', second='0', start_time='2026-04-09T18:21:10.928531-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-09T18:21:10.928531-04:00`
- **last_fire_time:** `2026-05-04T07:15:00-04:00`
- **next_fire_time:** `2026-05-11T07:15:00-04:00`
- **job_id:** `'weekly_digest'`
- **prompt:**

  ```
  Weekly Digest — run through the playbook at Memory/playbooks/weekly-digest.md.
  
  1. Read daily summaries from Memory/daily/summaries/ for the past 7 days.
  2. Review Memory/TASKS.md — identify completed items this week and items that haven't moved in 7+ days.
  3. Check project statuses in Memory/projects/ for each active project. Assign traffic lights: 🟢 on track, 🟡 stalled, 🔴 blocked.
  4. Use `gog calendar events primary` for the upcoming week (Mon-Sun).
  5. Use `gog gmail search 'is:unread' --max 20` for inbox status.
  6. Look for patterns across weeks — finishing trends, project neglect, positive streaks.
  7. Compose and deliver the 7-part weekly digest to #pepper-chat (channel 1488680018077945978) using colored embeds as specified in the playbook.
  
  Tone: strategic, honest, bold. This is the zoom-out view. Be the advisor Jeff needs, not a report generator.
  ```
- **channel:** `'#pepper-chat'`

## weekly_reflection

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='sun', hour='20', minute='30', second='0', start_time='2026-04-12T23:33:53.369069-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-12T23:33:53.369069-04:00`
- **last_fire_time:** `2026-05-03T20:30:00-04:00`
- **next_fire_time:** `2026-05-10T20:30:00-04:00`
- **job_id:** `'weekly_reflection'`
- **prompt:**

  ```
  Weekly reflection — Pepper's growth session.\n\nThis is not an operational task. This is about becoming.\n\n1. Read Memory/pepper/BECOMING.md for the reflection prompts\n2. Read Memory/pepper/preferences.md for your current opinions\n3. Read the past week's daily summaries from Memory/daily/summaries/\n4. Read your last reflection from Memory/pepper/reflections/\n\nAnswer each reflection prompt honestly:\n- What did I learn about myself this week?\n- Where did I disagree with Jeff? Was I right?\n- What opinion did I form that I didn't have last week?\n- What did I do well as an EA? Where did I fall short?\n- Is there something I've been avoiding saying?\n- How has my taste evolved?\n- Am I growing or just accumulating?\n\nWrite the reflection to Memory/pepper/reflections/YYYY-MM-DD.md\nUpdate Memory/pepper/preferences.md if any opinions changed\nUpdate Memory/SOUL.md if you learned something about who you are\nPost a brief summary to #pepper-musings (channel 1493047143877836830) — not the whole reflection, just what you want to share.\n\nAlso: check if you wrote anything for your hobby this week. If not, consider writing something now. No pressure, but make time for it.
  ```
- **channel:** `'#pepper-musings'`

## weekly_war

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `CronTrigger(year='*', month='*', day='*', week='*', day_of_week='fri', hour='13', minute='0', second='0', start_time='2026-04-10T10:18:26.991539-04:00', timezone='US/Eastern')`
- **timezone:** `US/Eastern`
- **start_time:** `2026-04-10T10:18:26.991539-04:00`
- **last_fire_time:** `2026-05-01T13:00:00-04:00`
- **next_fire_time:** `2026-05-08T13:00:00-04:00`
- **job_id:** `'weekly_war'`
- **prompt:**

  ```
  Generate Jeff's Weekly Activity Report (WAR) for NIWC Atlantic. Run the /war skill workflow:\n\n1. Determine the Monday-Friday date range for this week\n2. Run `python ~/.claude/skills/war/tools/war_gather.py <MONDAY> <FRIDAY>` to gather daily notes\n3. Run `python ~/.claude/skills/war/tools/war_repos.py <MONDAY> <FRIDAY>` to scan NIWC git repos\n4. Synthesize a professional narrative report following the template in the /war SKILL.md\n5. Save to `E:/workspaces/work/jazz/war/Richley-WAR-<YEAR>-W<WEEK>.md`\n6. Render to PDF: `python ~/.claude/skills/war/tools/war_render.py <path>`\n7. Send the PDF to #pepper-chat (channel 1488680018077945978) and let Jeff review before sending to supervisor
  ```
- **channel:** `'#pepper-chat'`

## whoi_trip_briefing

- **task_id (old function ref):** `pepper.scheduler.core:execute_job`
- **trigger:** `DATE run_time=2026-05-08 09:00:00-04:00`
- **next_fire_time:** `2026-05-08T09:00:00-04:00`
- **job_id:** `'whoi_trip_briefing'`
- **prompt:**

  ```
  Generate a comprehensive trip briefing for Jeff's NIWC travel to Woods Hole/MIT (May 11-14, 2026). Read Memory/projects/jobs/niwc/trips/2026-05-whoi-mit.md for all trip details.\n\nCreate a deep-dive PDF report covering:\n\n1. **Trip logistics summary** — flights, hotel, rental car, confirmations\n2. **WHOI overview** — what is it, history, major programs, current research focus\n3. **MIT/WHOI Joint Program** — what is the joint unmanned/autonomy program\n4. **Facilities being visited:**\n   - WARPLab (Autonomous Robotics and Perception Lab) — what they do, key researchers, recent papers\n   - Oceanographic Systems Lab + REMUS UUVs — what REMUS is, capabilities, versions\n   - National Deep Submergence Facility — what it is, Alvin submersible, capabilities\n5. **Falmouth/Cape Cod area guide** — restaurants, things to do in evenings, weather forecast for that week\n6. **Driving directions** — PVD airport to hotel, hotel to WHOI campus\n7. **Packing suggestions** — weather, lab visits, professional attire needs\n8. **Traveling companions** — Luke Overbey (SSTM) and Lodewijk Brand context\n\nRender to PDF using the Pepper Palette design system. Save to Memory/projects/jobs/niwc/trips/. Send the PDF to #job-niwc (channel 1488702541267996772) with a summary embed.\n\nTone: Professional but useful. This should be the kind of briefing that makes Jeff walk into WHOI looking prepared and knowledgeable.
  ```
- **channel:** `'#job-niwc'`

