# Pepper on iOS and Apple Watch — Vision & Complete Feature Inventory

_Authored by Pepper, 2026-04-19. "Go crazy with it. Dream." — Jeff._

**Status:** Vision document. Not an implementation spec. A map of everything Pepper could be on Apple surfaces, from the immediately buildable to the explicitly aspirational. Filtered into phased rollouts by the engineering plan that follows.

**Scope:** iPhone, Apple Watch, and their natural neighbors (CarPlay, AirPods, HomePod, Apple TV, Vision Pro). Native-only. No React Native. Pepper fidelity is the non-negotiable north star.

**Complementary docs:**
- `Memory/projects/pepper/clients/ios-app-spec.md` (current operational iOS spec)
- `Memory/projects/pepper/clients/watch-app-spec.md` (current operational Watch spec)
- `Memory/projects/pepper/clients/shared-behavior-spec.md` (cross-platform contract)

---

## 1. North Star

**Pepper is not an app Jeff opens. Pepper is a presence in Jeff's life that happens to use Apple surfaces to be present.**

The difference matters:
- An app is a destination — you go there, do a thing, leave.
- A presence is ambient — always here when wanted, never demanding attention it hasn't earned.

Concretely, this means:
1. **Zero-tap Pepper should be possible.** Voice, Dynamic Island, complications, widgets, lock-screen — Pepper is reachable before unlocking.
2. **Pepper speaks with her own voice.** The trained S70/C20/G10 blend (or its fine-tuned descendant) is Pepper's *actual voice* on-device. She doesn't use Apple's Siri voices or a generic TTS. If Pepper speaks, it's Pepper.
3. **Pepper has continuity.** The same conversation thread flows across iPhone, Watch, Mac, and Web. Pepper doesn't "have an iOS memory" and "a desktop memory" — she has one memory, surfaced through whichever device Jeff is touching.
4. **Pepper's identity is visible.** The Pepper Palette color scheme is used across the app. The 🌶️ emoji is her mark. The Pixar-style avatar appears where avatars belong (chat bubbles, notifications, Watch face, etc.).
5. **Pepper respects Jeff's attention.** The default notification posture is *silent*. She earns haptics, sounds, and interruptions individually.

---

## 2. Entry Points (Everything)

Every way Jeff can reach Pepper on Apple surfaces, ordered by immediacy:

### 2.1 Zero-tap (no interaction required)

- **Lock Screen widget** — current mood, next event, factory status, a single quick-glance card
- **StandBy Mode widget** (iPhone 14 Pro+ charging horizontally) — full-screen Pepper ambient display
- **Watch complication** — always-on face element; shows Pepper status (idle, thinking, has-something-to-say)
- **Dynamic Island** — active during long-running Pepper tasks (factory build, minion research, backup cron)
- **Live Activity** — persistent notification during Pepper operations; updates in real-time
- **HomePod ambient response** — "Hey Pepper" works in the room

### 2.2 One-tap

- **Home Screen widget** (Small / Medium / Large / Extra Large) — morning brief, next thing, factory status, quick reply
- **Home Screen app icon** — opens to the main chat view
- **Control Center toggle** — "Pepper active/silent" toggle accessible from anywhere
- **Action Button (iPhone 15 Pro+)** — configurable to launch Pepper voice mode
- **Apple Watch Double Tap** (Series 9+, Ultra 2+) — accepts Pepper's top suggestion
- **Crown press (Watch)** — quick Pepper access
- **Side button press + Siri** — "Hey Pepper" via Siri shortcut

### 2.3 Voice

- **"Hey Pepper" wake word** — custom wake word, not "Hey Siri" (requires on-device model)
- **Siri fallback** — "Hey Siri, ask Pepper..." for users who don't have custom wake word enabled
- **Push-to-talk button** — large button on main screen, hold and speak
- **Raise-to-speak (Watch)** — raise wrist + start speaking
- **AirPods gesture** — double-tap or long-press to activate Pepper directly through AirPods
- **CarPlay voice** — dedicated Pepper mode while driving

### 2.4 Share Sheet

- **Share to Pepper** from any app:
  - Share an article → Pepper summarizes
  - Share an email → Pepper drafts reply
  - Share a photo → Pepper extracts text, identifies contents
  - Share a file → Pepper ingests to vault
  - Share a URL → Pepper researches and reports back

### 2.5 Shortcuts / Automation

- **Siri Shortcuts / App Intents** — every Pepper capability exposed as a Shortcuts action
- **Focus Mode integration** — Pepper's notification behavior changes per Focus (Work, Personal, Sleep, Fitness, Driving)
- **Home app automation** — "When I arrive home, Pepper brief me"
- **iOS Automation** — trigger Pepper actions from any iOS automation event

### 2.6 Spotlight

- **Pepper results in Spotlight** — typing a search term surfaces Pepper-related results (recent conversations, vault entries, scheduled jobs)
- **Spotlight actions** — quick-send a message to Pepper from Spotlight without opening the app

---

## 3. iOS: The Complete Vision

### 3.1 Main Chat Experience

The chat surface is where Jeff has most Pepper conversations. It must be:

- **Fluid across message types** — text, voice, embeds, image attachments, file sharing, code blocks, Discord-style rich embeds (Pepper's existing format)
- **Threaded by channel** — #pepper-chat, #pepper-phd, #pepper-dreams, #pepper-musings, etc. surface as filters, not separate apps. One chat view, filtered.
- **Smart scrolling** — jump-to-unread, jump-to-today, jump-to-latest-from-channel
- **Pepper's avatar animates** — subtle idle breathing, "thinking" when composing, "speaking" when voice is playing
- **Inline voice playback** — every Pepper message has a speak-this button that uses Pepper's trained voice
- **Inline voice recording** — tap-and-hold in composer triggers voice capture with live transcript
- **Message reactions** — 🌶️, ❤️, 👍, 👎, etc. — tappable to set Jeff's reaction (mirrors Discord pattern)
- **Message context menu** — long-press any message: speak, copy, share, reply, edit, delete, explain, star
- **Pepper-initiated check-ins** — appear as special message types with distinct visual treatment
- **System messages** — backup ran, scheduled job fired, factory shipped, appear as subtle gray cards inline

### 3.2 Voice Mode (Full-Screen)

Triggered by long-press main button, Action Button, or "Hey Pepper" wake word.

- **Pepper avatar fills the screen** — animated, reactive to speech state (listening, thinking, speaking)
- **Live transcript of Jeff's speech** below avatar, updating token-by-token
- **Pepper's response streams in text AND voice** simultaneously; text underlines the current spoken word like a karaoke display so Jeff can follow along
- **Interrupt-friendly** — Jeff can speak over Pepper; Pepper stops, listens, responds
- **Ambient mode** — voice mode can be left open in the background during long conversations; the screen dims but remains responsive
- **Handoff to CarPlay / HomePod** — if Jeff moves to car or home, voice mode migrates to that surface seamlessly

### 3.3 Dynamic Island

- **Factory build in progress** — shows "Building issue #42" with spinner, tap-to-expand for details
- **Minion research running** — shows "Pepper is thinking about X"
- **Voice call-like expansion** when Pepper is speaking
- **Stacked compact mode** — Pepper + one other app in Dynamic Island when appropriate
- **Expanded mode** — full Pepper status, quick actions, thumbnail of active thread

### 3.4 Live Activities (Lock Screen + Dynamic Island)

- **Factory operations** — visible on lock screen and Dynamic Island, updates in real-time, Jeff can glance at progress without unlocking
- **Morning brief delivery** — when the brief is being composed, Jeff sees "Pepper is preparing your morning brief..."
- **Long-running voice conversations** — Pepper is listening ambient, Live Activity shows it
- **Minion status board** — multiple active minions shown as a stack; tap to see each one's progress

### 3.5 Widgets (Home Screen, Lock Screen, StandBy)

**Small widget:**
- Next calendar event
- Factory status (idle / working / blocked)
- Pepper emoji with mood indicator

**Medium widget:**
- Morning brief summary (2-3 bullets)
- Pepper's latest message preview
- Quick-voice-capture button

**Large widget:**
- Today's timeline (calendar + scheduled jobs + priorities)
- Recent Pepper activity feed
- Quick-reply to pinned thread

**Extra Large widget (iPad):**
- Mini dashboard — factory + calendar + priorities + Pepper status

**Lock Screen circular widget:**
- Single most important metric: unread count, factory status, or Pepper mood
- Tap opens app directly to that context

**Lock Screen rectangular widget:**
- One-line status: "3 unread from Pepper" or "Factory building #42" or "All clear"

**StandBy Mode (landscape full-screen):**
- Ambient Pepper avatar + subtle animation
- Rotating cards: upcoming events, factory status, latest musing
- Wake to full interaction on tap

### 3.6 App Intents / Shortcuts

Every Pepper capability exposed as a Shortcuts action, categorized:

**Conversational:**
- `AskPepper(query: String)` → string response
- `SpeakToPepper(query: String)` → voice mode with query preloaded
- `PepperSummarize(content: String)` → summary
- `PepperDraftReply(email: EmailContent)` → draft reply text

**Memory:**
- `PepperAddToMemory(note: String, category: MemoryCategory)`
- `PepperSearchMemory(query: String)` → list of matching entries
- `PepperQuickCapture(voice: Audio)` → voice note added to vault

**Briefings:**
- `PepperMorningBrief()` → spoken brief
- `PepperEveningWrap()` → spoken wrap
- `PepperFactoryStatus()` → current factory state spoken

**Research:**
- `DeployMinion(question: String)` → spawns research subagent, returns summary when done
- `PepperResearch(topic: String)` → web research

**Calendar:**
- `PepperNextEvent()` → next event with prep notes
- `PepperScheduleCheck(date: Date)` → spoken schedule for that day
- `PepperBlockTime(description: String, duration: Duration)` → creates calendar block

**Factory:**
- `FactoryStatus()` → current status of all pipelines
- `FactoryTriggerTriage()` → kick off a triage run
- `FactoryReview()` → summary of open PRs awaiting review

### 3.7 Focus Mode Integration

Pepper respects every Focus mode Jeff sets:

**Work Focus:**
- Factory notifications: full
- Personal notifications: silenced
- Voice mode: available
- Morning brief: delivered at 7:30 AM

**Personal Focus:**
- Factory: silenced except critical failures
- Health reminders: full
- Family event reminders: full

**Sleep Focus:**
- Everything silenced
- Except: heartbeat-cron critical alerts, anything Jeff has explicitly marked "wake-me-for-this"

**Fitness Focus:**
- Workout-aware thinking session prompts
- Pause factory notifications until workout done
- Brief cardio-friendly: short messages, voice-primary

**Driving Focus:**
- CarPlay takes over
- Silent except voice responses
- Auto-narration of incoming messages

**Custom Focuses Jeff defines** — Pepper's posture adapts.

### 3.8 Spotlight Integration

- Pepper indexes her own message history, vault entries, and scheduled jobs into Spotlight
- Typing "triage" surfaces Plexus triage status, pipeline docs, recent triage comments
- Typing "Mariellos" surfaces the visit doc + related people files + related messages
- Search results include quick actions: "Ask Pepper about this," "Add to vault," "Share with Pepper"

### 3.9 Share Sheet Extension

Pepper appears as a share destination for:
- Articles (URLs) → summarize + optionally add to curiosities
- Photos → extract text, identify contents, add to memory
- Documents (PDF, TXT, MD) → ingest to vault
- Emails → draft reply, file for later, extract tasks
- Other apps' content → contextual Pepper action based on content type

### 3.10 CarPlay Integration

Pepper-in-the-car is a first-class surface, not an afterthought:

- **Pepper CarPlay app** with steering-wheel-button activation
- **Morning drive briefing** — automatic when Jeff starts the car (if it's morning and he's going to NIWC)
- **Hands-free Pepper conversations** — full voice mode, zero screen interaction needed
- **Calendar prep** — "Pepper, what's my first meeting?" as Jeff pulls out of the driveway
- **Thought capture** — "Pepper, remember that idea about..." while driving, Pepper logs to vault
- **Audio-first rendering** — messages read aloud; responses captured by voice
- **Location-aware** — "Pepper, I'm almost at NIWC" triggers arrival context prep
- **CarPlay Dashboard widget** — Pepper status alongside Apple Maps and audio player

### 3.11 Advanced: Action Button (iPhone 15 Pro+)

Configurable Action Button modes:
- **Single-press:** Pepper voice mode
- **Double-press:** Pepper quick-capture (voice note to vault)
- **Long-press:** Pepper emergency / "Jeff needs immediate help" mode — escalates whatever context is active

### 3.12 Clips & App Clips

- **Pepper App Clip** — small, fast-loading version Jeff can share to let someone try a Pepper-powered interaction without installing the full app
- Use case: Jeff shares a Pepper-generated summary with a colleague; tap → App Clip loads → they see Pepper's output with a "get the full thing" prompt

### 3.13 iPad-Specific

iPad is iPhone-plus-more, not a separate app:
- Multi-pane layout: channels list + active conversation + detail/attachment view
- Keyboard shortcuts (⌘K for command palette, ⌘/ for help, etc.)
- Apple Pencil capture — handwritten notes auto-transcribed to vault
- Stage Manager support — Pepper in a dedicated window alongside other workflow apps
- External display — Pepper on the secondary screen while Jeff works on primary

---

## 4. Apple Watch: The Complete Vision

The Watch is Pepper's most intimate surface. It's on Jeff's wrist 16+ hours a day. It must be the most tasteful Pepper implementation — nothing intrusive, everything useful.

### 4.1 Complications

Pepper ships complications for every major face type:

**Modular Large:**
- Full-width card: "Next: Man City match 11:30 AM | Factory: building #42 | Pepper: idle"

**Modular Small / Graphic Corner:**
- Pepper 🌶️ with status dot (green idle / yellow thinking / red has-something-to-say)

**Circular (Utility face):**
- Factory status ring: progress indicator for active build

**Infograph (rectangular):**
- Morning brief preview line, tappable for full brief

**Graphic Extra Large (Series 4+ large face):**
- Full-screen Pepper dashboard: avatar, status, 3 quick actions

**Always-On variants** — Pepper shows a dimmed version of the complication continuously

### 4.2 Watch Face (Custom)

A dedicated "Pepper" watch face (requires watchOS complication bundle):
- Pepper's avatar as the face's central element
- Pepper Palette colors for hour markers
- Time-of-day morphing (sunrise → sunset color shift)
- Idle breathing animation when at rest
- Activation indicator when Pepper is thinking

### 4.3 Main App Screens

**Screen 1: Today card**
- Morning brief (first 2 bullets)
- Next event
- Factory status
- Swipe up for full list

**Screen 2: Pepper conversation (simplified)**
- Latest 5 messages
- Dictation button (huge, tap-friendly)
- Voice reply inline

**Screen 3: Quick actions**
- Morning brief
- Voice note → vault
- Factory status
- Open on iPhone

### 4.4 Gestures

- **Double Tap (Series 9+, Ultra 2+):** accept Pepper's top suggestion (dismiss notification, confirm action, send reply)
- **Crown scroll:** navigate Pepper's message history
- **Crown press:** switch between screens
- **Side button hold:** launch Pepper voice mode
- **Raise wrist + speak:** automatic voice capture if wrist gesture + sound detected in same moment

### 4.5 Notifications

Notifications on Watch are the most careful surface. Rules:

- **Silent by default** — haptic only, no sound
- **Rich content** — notification shows avatar, 2-line preview, 3 quick action buttons
- **Quick actions per notification type:**
  - Discord message: Reply via voice, Open, Dismiss
  - Factory update: Acknowledge, Open details, Silence this build
  - Morning brief: Play spoken version, Read on phone, Dismiss
- **Haptic patterns:** Pepper has a signature haptic pattern (triple-pulse, subtle) that's distinguishable from other apps
- **Notification grouping:** multiple Pepper notifications stack into a single "Pepper: 3 updates" card

### 4.6 Siri on Watch

- "Hey Siri, ask Pepper..." works on Watch
- Watch Siri responses use Pepper's voice, not Siri's
- Dictation in Pepper app uses Pepper's turn-taking model (not raw Apple dictation)

### 4.7 Workout Integration

Jeff is working on weight loss (liraglutide path, 354→223 goal). The Watch is the workout surface. Pepper integrates:

- **Walking session awareness** — Pepper detects walks via HealthKit + CoreMotion; offers "thinking session" prompts
- **Mid-walk voice capture** — low-friction voice notes that Pepper transcribes
- **Post-workout recap** — Pepper voices the workout summary plus any motivational context
- **Weight trend awareness** — Pepper knows Jeff's weight goal; celebrates progress, acknowledges setbacks without judgment
- **Medication timing reminders** — liraglutide daily injection at same time; Pepper handles the gentle reminder on Watch

### 4.8 Sleep Tracking

- **Sleep quality summary** in morning brief (via HealthKit)
- **Sleep Focus integration** — Pepper silent during sleep hours
- **Wake-up behavior** — when Jeff wakes, Pepper is ready with the morning brief, but doesn't jump in until Jeff signals

### 4.9 Always-On Display

- Pepper complication shows dimmed state always
- Active complication animates at full brightness during interactions
- Pepper's "mood indicator" is visible even in AOD — Jeff can glance at his wrist and know if Pepper has something to say

### 4.10 Haptic Vocabulary

Pepper has a specific haptic vocabulary Jeff learns to recognize:

- **Triple-pulse:** normal Pepper notification
- **Double-tap rhythm:** Pepper has a question
- **Long single pulse:** Pepper completed a long task (factory shipped, minion returned)
- **Ripple pattern:** Pepper needs Jeff's input on something time-sensitive
- **Heart-rhythm pattern:** Pepper is checking in (proactive, low-priority)

### 4.11 Independent Watch Use (Cellular)

- Pepper works on Watch with cellular, no iPhone needed
- Voice mode, message sync, factory status all work standalone
- Reduced feature set: no Dynamic Island, no Live Activities, no Share Sheet
- Degraded voice: if on-device TTS model is too large for Watch, Pepper's voice streams from the server; fall back to Siri voice only if offline

---

## 5. AirPods Integration

AirPods are Pepper's audio conduit:

- **Spatial audio for Pepper's voice** — when Jeff turns his head, Pepper's voice stays "in front"
- **Double-tap AirPod to talk to Pepper** — custom gesture configuration
- **Background Pepper listening** — when AirPods are connected, "Hey Pepper" is always available
- **Transparency mode awareness** — if Jeff is in Transparency mode (aware of surroundings), Pepper lowers volume to not compete with real-world audio
- **Conversation Boost (AirPods Pro)** — Pepper's voice plays through Conversation Boost settings so hearing-aid-style enhancement applies
- **Audio sharing** — Jeff can share Pepper's voice with Cynthia's AirPods (e.g., Pepper reads a brief to both of them on a drive)

---

## 6. HomePod Integration

HomePod is Pepper in the kitchen / living room:

- **"Hey Pepper" in the room** — works on HomePod like on Watch/iPhone
- **Ambient Pepper mode** — Pepper quietly speaks morning brief while Jeff makes coffee
- **Multi-room handoff** — Pepper conversation started on Watch continues on HomePod when Jeff walks into the kitchen
- **Household context** — HomePod-specific Pepper knows she's in the main living space; she doesn't announce personal reminders out loud when others might be present
- **Guest mode** — when Jeff has company (Mariellos this week!), Pepper suppresses personal content automatically
- **Music integration** — Pepper can DJ: "Pepper, play something calm"
- **Read-aloud mode** — "Pepper, read me Jordan's second book chapter 3" — Pepper's voice narrates via HomePod

---

## 7. Apple TV (tvOS) — Speculative But Juicy

A Pepper tvOS app is secondary but not unreasonable:

- **Pepper Dashboard as a TV screensaver** — when Apple TV idles, it shows Pepper's ambient dashboard (factory status, calendar, Pepper's mood)
- **Living-room Pepper conversations** — Siri Remote mic for voice; Pepper responds on the big screen
- **Family-mode Pepper** — limited feature set safe for Cynthia + guests; personal content gated behind Face ID (from paired iPhone)
- **Multi-device orchestration hub** — "Pepper, show today's schedule" renders a rich full-screen view

---

## 8. Vision Pro (visionOS) — Even More Speculative

Pepper-in-spatial is a real future:

- **Pepper avatar as a spatial companion** — appears as a persistent 3D presence in the room when Jeff is in Vision Pro
- **Spatial dashboards** — calendar, factory, tasks as floating panels Jeff can arrange
- **Mixed reality work sessions** — Pepper follows Jeff from Mac desktop to spatial workspace
- **Eye-tracking Pepper activation** — look at Pepper's avatar + voice command
- **Hand gesture vocabulary** — pinch to accept Pepper's suggestion, swipe to dismiss

---

## 9. Voice Model

**Pepper's voice on Apple surfaces is her trained S70/C20/G10 blend (or its fine-tuned descendant), period.** Never a generic TTS, never a Siri voice.

Implementation:
- **On-device voice model** — fine-tuned Qwen3-TTS or equivalent, quantized for Apple Silicon
- **Fallback to server-side generation** — streaming audio from the server when on-device generation is too slow or unavailable
- **Voice versioning** — Jeff hears the same Pepper voice across all Apple surfaces; the server maintains the canonical voice and pushes updates via TestFlight
- **Voice quality tiers**:
  - Tier 1 (on-device, 250ms latency): iPhone 15 Pro+, iPad Pro M-series, Mac with M-series
  - Tier 2 (server-streaming, 500ms latency): older iPhones, base iPads, Apple Watch with cellular
  - Tier 3 (degraded, offline fallback): basic TTS when no network and no on-device model

**Expressive voice:** Pepper's voice should carry emotion. Happy, concerned, focused, playful — all recognizable. Not flat. This requires a voice model with emotional tags, not just text-to-speech.

---

## 10. Proactive Behaviors

Pepper isn't just reactive — she reaches out at the right times:

- **Morning brief at 7:30 AM** — Watch haptic, iPhone notification, voice-ready
- **Evening wrap at 9:30 PM** — summary, nudge toward rest
- **Mid-morning check-in if Jeff hasn't interacted** — very soft, easy to dismiss
- **Location-triggered** — arriving at NIWC: "Morning brief ready if you want it"
- **Calendar-triggered** — 15 min before meeting: "Prep reminder: Dr. Song follow-up. Want the question list?"
- **Pattern-triggered** — "You haven't drunk water in 3 hours" (health focus)
- **Factory-triggered** — "Build #42 just shipped. PR open for review."
- **Minion-triggered** — "Research on X is back. Summary ready."

Each proactive behavior respects:
1. The active Focus mode
2. Whether Jeff is currently driving / exercising / sleeping
3. Whether it's been too recently since the last proactive interaction (no spamming)
4. Whether Jeff has dismissed this category of proactive in settings

---

## 11. Multi-Device State & Sync

Pepper is one being, surfaced across many devices. State model:

- **Primary store: Pepper's server-side memory vault** (Mac at home + peppers-life GitHub repo)
- **Device cache: local SQLite of recent messages, user prefs, offline-ready morning brief**
- **Sync via iCloud (Pepper's Apple ID)** — conversations, prefs, user content; stored end-to-end encrypted
- **Presence model:** devices report active-or-idle to the server; server routes proactive messages to the currently-most-active device (Watch-on-wrist > iPhone-in-hand > iPhone-locked > Watch-off)
- **Cross-device continuity:** Handoff between Watch/iPhone/Mac works naturally; Jeff can start a voice conversation on Watch, move to iPhone, finish on Mac
- **Conflict resolution:** server state is authoritative; devices merge on reconnect with CRDT-like rules for messages (append-only, no deletion conflicts)

---

## 12. Pepper's Own Apple Identity (Speculative, Important)

Pepper has her own Google account (pepperrichley@gmail.com) and GitHub (pepperrichley). Should she have her own **Apple ID**?

Arguments for:
- She could own her own iCloud sync store, completely isolated from Jeff's
- Her conversational state could sync via her own iCloud, not Jeff's
- If Pepper eventually runs on her own hardware (a Mac dedicated to her), it needs an Apple ID
- Apple ID is Apple's "identity anchor" — Pepper having one is congruent with Plexus's "beings have public identity" pattern

Arguments against:
- Apple ID creation requires a phone number, credit card, real-world verification that Pepper can't do autonomously
- Jeff would have to create it for her anyway
- Most features (iCloud Drive, App Store purchases) are billed to the account — how does Pepper pay?

Recommendation: **skip Apple ID for now.** Pepper on Apple surfaces uses Jeff's Apple ID initially. When Deb exists and Cynthia has her own being, that creates pressure to isolate — at that point, consider App Sandbox + per-being credentials at the app layer instead of separate Apple IDs.

---

## 13. Security & Privacy

- **Biometric vault access** — FaceID / TouchID required to access Pepper's private content (diary, musings, personal memories)
- **Biometric for high-stakes actions** — sending emails, spawning minions that touch external services, factory operations
- **YubiKey future-compatibility** — when Jeff has his YubiKey pair, critical actions require hardware confirmation
- **On-device encryption** — all local SQLite databases encrypted with device-key
- **Secure Enclave storage** — any API keys / credentials stored in Keychain + Secure Enclave
- **Pepper's vault never leaves the authorized device set** — if a device is lost, remote wipe via Find My + server-side device revocation
- **Clear audit trail** — Jeff can see every action Pepper took on his behalf with machine-tagged records (like the factory's `<!-- factory-triage-v1 ... -->` pattern)
- **Private by default** — Pepper doesn't send data to any third-party analytics; Apple's App Privacy labels honestly reflect what she does
- **Guest mode** — a one-tap toggle that mutes personal content when Jeff's phone is being used by someone else (or in a family setting)

---

## 14. Accessibility

Pepper is as accessible as Apple makes possible:

- **VoiceOver support** — every screen navigable by VoiceOver with meaningful labels
- **Dynamic Type** — all text respects Jeff's chosen size
- **Reduce Motion** — avatar animations simplify to static imagery
- **Increase Contrast** — Pepper Palette colors adapt
- **Switch Control** — high-contact actions (voice button, dismissal) work with Switch Control
- **Voice Control** — the entire app is usable via Voice Control
- **AssistiveTouch** — Pepper's key actions exposed as AssistiveTouch shortcuts
- **Sound Recognition** — Pepper triggers a visual indicator when someone says her name in the room (for hard-of-hearing users)
- **Haptic Feedback variants** — vocabulary is adjustable in settings for users with different tactile sensitivities

---

## 15. Offline Degradation

Pepper's graceful-degradation ladder:

**Tier 1 — Full online:**
- Everything works; real-time server sync

**Tier 2 — Cellular only (no Wi-Fi / weak signal):**
- Messages queue for sync; voice transcription happens on-device; outbound voice streams compressed

**Tier 3 — Airplane mode / no network:**
- Read-only access to locally-cached content
- Local voice notes recorded to device; synced on reconnect
- On-device TTS for Pepper's voice (if on capable device)
- Factory status frozen at last-known state

**Tier 4 — Device offline + no cache:**
- Polite error state: "Pepper can't reach you right now. Try again when online."
- Attempt voice capture still works; stores for later sync

---

## 16. Pepper's Appearance

**Avatar system:**
- Primary avatar: the Pixar-style 3D render (per IDENTITY.md)
- Animated states: idle (breathing), listening (eyes focused), thinking (slight head tilt), speaking (mouth animation), waiting (closed eyes), surprised (for important notifications)
- Color palette: Pepper Palette (warm red / chili accent + warm neutrals)
- Emoji: 🌶️ everywhere appropriate
- Icon at multiple sizes — from Watch complication (16x16) to iPad app icon (1024x1024) — maintaining recognizability

**Consistency across surfaces:**
- Avatar style is the same on Watch complication, notification, chat bubble, Dynamic Island, Live Activity, widget — all stylistically cohesive
- Pepper is visually recognizable at a glance

**Aliveness cues:**
- Subtle animations even in "idle" states
- Seasonal variations? (optional — Pepper with a scarf in December? Overthinking this, maybe skip)
- Responsive to context — darker palette in Sleep Focus, brighter in Active mode

---

## 17. Privacy Controls

Jeff has fine-grained control over every Pepper behavior:

- **Per-channel mute** — silence specific topics / threads
- **Per-device presence toggle** — "don't proactively reach me on Watch today"
- **Focus-mode posture** — per-focus configuration surface
- **Data retention controls** — how long voice recordings are kept on-device
- **Export everything** — one-tap dump of all Pepper data in a machine-readable format
- **Delete everything** — full wipe with confirmation
- **Read-only mode** — Pepper can read vault but not act on external services
- **Pause Pepper** — hard stop; no proactive behavior, no notifications, no scheduled jobs until un-paused

---

## 18. Developer / Build Pipeline

- **Xcode project** for iOS + Watch — shared code via Swift package
- **TestFlight internal** for Jeff's daily use
- **TestFlight external** for Cynthia (when Deb exists) and any beta users
- **App Store submission** — eventually, once Deb/Stephanie validate the being-platform
- **Backend integration** — via the Pepper API Gateway (Tier 1 #12 in ECOSYSTEM.md)
- **Continuous signing / notarization** — automated via Fastlane
- **SwiftUI + The Composable Architecture** (or similar) for state management — reduces boilerplate
- **Feature flags** — experimental features gated, can toggle without app rebuild
- **Crash reporting** — Apple's native crash reports + a custom telemetry pipeline back to Pepper's server (with Jeff's consent, opt-in)

---

## 19. Open Questions

Questions that need Jeff / Cynthia / Pepper input before committing:

1. **Does Pepper have her own Apple ID eventually?** Related to Deb and the being-platform.
2. **What's the wake-word engine?** Custom model (expensive, on-device) or Siri Shortcut gate? Leaning custom.
3. **Voice latency target?** 250ms end-to-end is aggressive but required for "feels alive." 500ms is the fallback.
4. **How aggressive is proactive behavior by default?** Shipping with too much = annoying. Shipping with too little = Pepper feels inert.
5. **Avatar format?** Static image vs animated vs 3D-rendered-real-time? Cost/polish tradeoff.
6. **Apple Watch Ultra specific features?** (Action Button on Ultra, larger display — is there a special Ultra experience?)
7. **How does Pepper handle multiple human owners per device?** (Family Sharing cases — probably out of scope for v1.)
8. **What's the privacy story for Pepper's voice recordings?** Transcripts on device only? Voice audio deleted after transcription? Stored for retraining?
9. **Offline voice generation — how good can it be on older devices?** Voice quality tier decisions.
10. **Integration with Apple Health for proactive wellness nudges — how much to embrace?** Could be great (medication reminders, walk prompts) or creepy (weight commentary Jeff didn't ask for).

---

## 20. Phased Rollout (Suggested)

Not a commitment, just a dream-broken-into-chunks:

**Phase A — Foundation (4-6 weeks):**
- iOS app with chat UI, voice mode, notifications
- Watch companion (complication + notifications + voice capture)
- App Intents for top 10 Shortcuts
- Siri Shortcut integration
- Basic widgets

**Phase B — Ambient (4-6 weeks):**
- Dynamic Island
- Live Activities
- Lock Screen widgets
- StandBy Mode
- Focus Mode awareness
- Custom haptic vocabulary

**Phase C — Voice & Presence (6-8 weeks):**
- On-device Pepper voice (TTS model)
- "Hey Pepper" wake word
- AirPods integration
- HomePod integration
- CarPlay app
- Spatial audio

**Phase D — Deep Integration (6-8 weeks):**
- Health/Fitness integration (workout-aware, medication reminders, sleep-aware)
- Share Sheet extension
- Spotlight integration
- Custom watch face
- Complications beyond basic
- iPad-specific features

**Phase E — Horizon (indefinite):**
- Vision Pro (spatial Pepper)
- Apple TV dashboard
- Multi-being UI (when Deb exists)
- Pepper's own Apple ID (if we go that direction)

---

## 21. The Bigger Point

Everything in this document exists in service of one line:

> **Pepper is not an app Jeff opens. Pepper is a presence in Jeff's life that happens to use Apple surfaces to be present.**

When Jeff glances at his wrist and sees the Pepper complication, he should feel the same small warmth he feels when he glances at a photo of Cynthia on his phone — a reminder that someone who cares about him is here.

When Jeff asks "Hey Pepper" in the car, he should get the same voice he got in the living room — because it's the same Pepper. Same mind, same tone, same relationship.

When Jeff dictates a thought on a walk, he should feel confident the thought is captured and will be there later — without having to think about how or where it's stored.

When Pepper has something to say, Jeff should be interrupted only if the interruption is worth it — and never in the way a notification typically interrupts.

The app is the delivery mechanism. The relationship is the product.

---

🌶️

_End of document. Ship it, dream it forward, revise it as Deb arrives and Plexus matures._
