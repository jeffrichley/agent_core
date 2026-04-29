- `DiscordEndpoint` adapter — bridges one Discord bot to one named bus
  agent (1:1). Inbound messages and user reactions become `TextMessage` and
  `Event` envelopes; outbound `ToolInvocation` envelopes dispatch to 7
  Discord tools (`send`, `edit`, `react`, `fetch`, `download_attachments`,
  `list_channels`, `get_channel_info`). Replies via `Acknowledgment`
  envelopes. Access control via JSON config (DM policy + channel allowlist
  + ack emoji) ports verbatim from Pepper.
