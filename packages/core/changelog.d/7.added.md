- `SchedulerEndpoint` adapter — fires scheduled prompts as bus envelopes.
  Static `jobs.yaml` seeds at boot plus dynamic management via
  `ToolInvocation` envelopes addressed to `to=scheduler`. Six tools:
  `create_job`, `update_job`, `delete_job`, `list_jobs`, `pause_job`,
  `resume_job`. Replies via `Acknowledgment` envelopes back to caller.
