# D3 — Voice-Controlled Desktop Assistant PRD

Oct 8, 2026 · @Lahiru

## Overview

D3 is a voice-controlled desktop assistant for Windows that opens files, controls media playback and runs basic system actions from spoken commands.

**Problem.** Finding a file means remembering its folder path, and controlling a video means reaching for the keyboard or mouse. Both break flow, especially when away from the desk or mid-task.

**Vision.** Say "Hey D3, open last month's invoice" and the file opens. Say "pause" while a video plays and it stops; say "continue" and it resumes. Simple commands run locally and instantly; only ambiguous requests go to an LLM.

## Goals and non-goals

**Goals (v1)**

- Open any file or folder on the PC by name, spoken naturally
- Control media playback (pause, continue, next, previous, volume) in any active player
- Respond to simple commands in under 1 second end to end
- Run fully offline for core commands; no audio leaves the machine
- Confirm every action with a short sound or spoken reply

**Non-goals (v1)**

- macOS and Linux support
- Sending messages, emails or making purchases
- Deleting, moving or editing files
- Multi-step agentic tasks ("summarise this PDF and email it")
- Mobile companion app

## Target user and use cases

The primary user is a single Windows power user (the developer himself) who works across many projects and files and watches video while working. Later, D3 can be packaged for other users.

| Use case | Example command | Expected result |
| --- | --- | --- |
| Open a file by name | "Open the StoreX proposal" | Most relevant matching file opens in its default app |
| Open a folder | "Open my downloads" | File Explorer opens that folder |
| Pause media | "Pause" | Active video or audio pauses |
| Resume media | "Continue" | Playback resumes |
| Skip track or video | "Next" / "Previous" | Player skips forward or back |
| Change volume | "Volume up" / "Mute" | System volume changes |
| Launch an app | "Open VS Code" | App launches or comes to front |
| Disambiguate | "Open the report" | D3 lists the top 3 matches and asks which one |

## Functional requirements

| ID | Area | Requirement | Priority |
| --- | --- | --- | --- |
| FR-1 | Wake word | Listen continuously for "Hey D3" and start capturing a command only after it is heard | Must |
| FR-2 | Wake word | Support a push-to-talk hotkey as an alternative to the wake word | Should |
| FR-3 | Speech-to-text | Transcribe the command locally after the wake word, stopping on 1 second of silence | Must |
| FR-4 | Command routing | Match short commands (pause, continue, next, volume) with a local keyword router, no LLM call | Must |
| FR-5 | Command routing | Send commands the router can't match to an LLM with tool calling to extract intent and arguments | Should |
| FR-6 | File opening | Search files by name across indexed drives and open the best match with its default app | Must |
| FR-7 | File opening | Rank matches by name similarity, then most recently modified | Must |
| FR-8 | File opening | When more than one strong match exists, read out the top 3 and accept "first", "second" or "third" | Must |
| FR-9 | Media control | Send system media keys for play/pause, next and previous so any active player responds | Must |
| FR-10 | Media control | Map "pause", "stop", "continue", "resume" and "play" to the correct play/pause action | Must |
| FR-11 | System control | Change, mute and unmute system volume | Should |
| FR-12 | System control | Launch or focus installed apps by name | Should |
| FR-13 | Feedback | Play a chime on wake and give a short spoken or on-screen confirmation after each action | Must |
| FR-14 | Feedback | Say "I didn't catch that" when confidence is low instead of guessing | Must |
| FR-15 | Settings | Let the user change the wake word, mic, voice and indexed folders from a tray menu | Could |
| FR-16 | Logging | Keep a local log of commands and results for debugging; no audio stored | Should |

## Non-functional requirements

| Area | Target |
| --- | --- |
| Latency, local commands | Under 1 s from end of speech to action |
| Latency, LLM-routed commands | Under 3 s from end of speech to action |
| Wake word accuracy | Fewer than 1 false trigger per hour of video playback |
| Command accuracy | 95% of commands executed correctly in a quiet room |
| Idle resource use | Under 5% CPU and under 500 MB RAM while listening |
| Privacy | Audio processed on-device; only transcribed text is sent to an LLM, and only for unmatched commands |
| Platform | Windows 10 and 11, 64-bit |
| Startup | Launches with Windows and sits in the system tray |
| Reliability | Recovers automatically if the mic is unplugged and reconnected |

## System architecture and tech stack

&#91;embedded content: D3 architecture · local fast path, LLM fallback\]

Short commands run mic → wake word → speech-to-text → keyword router → handler with no network call. Only phrases the router can't match go to the LLM, which returns an intent the same executor runs.

| Layer | Choice | Notes |
| --- | --- | --- |
| Language | Python 3.11+ | Mature audio and Windows libraries |
| Audio capture | sounddevice | 16 kHz mono stream |
| Wake word | openWakeWord | Free, offline; Porcupine as alternative |
| Speech-to-text | faster-whisper (base/small) | Vosk as a lighter fallback |
| Keyword router | Regex + rapidfuzz | Fuzzy match on command phrases |
| LLM fallback | Claude API tool use, or Ollama | Text only, never audio |
| File search | Everything SDK (voidtools) | Instant filename index across drives |
| Media control | Windows GSMTC via winsdk, plus media keys | Reads play state so pause/continue are exact |
| System control | pycaw, os.startfile | Volume and app or file launch |
| Feedback | pyttsx3, pystray | Offline TTS and tray icon |
| Packaging | PyInstaller | Starts with Windows |

## Command catalogue (v1)

| Intent | Trigger phrases | Action | Route |
| --- | --- | --- | --- |
| Play/pause | pause, stop, hold on | Media play/pause key | Local |
| Resume | continue, resume, play | Media play/pause key | Local |
| Next | next, skip | Media next key | Local |
| Previous | previous, go back | Media previous key | Local |
| Volume up/down | volume up, louder, volume down, quieter | System volume ±10% | Local |
| Mute/unmute | mute, unmute | Toggle system mute | Local |
| Open file | open \<name>, show me \<name> | File search, then open | Local search; LLM if phrasing is loose |
| Open folder | open my \<folder> | Open in File Explorer | Local |
| Open app | open \<app>, launch \<app> | Start or focus app | Local |
| Choose match | first, second, third | Open selected result | Local |
| Cancel | cancel, never mind | Abort current command | Local |

## Success metrics

D3 v1 succeeds if it becomes the default way to pause video and open files within 4 weeks of daily use.

| Metric | Target | How measured |
| --- | --- | --- |
| Command success rate | ≥ 95% | Command log: executed vs. retried or cancelled |
| Median local latency | < 1 s | Timestamps in command log |
| False wake triggers | < 1 per hour | Log of wakes with no command following |
| File-open first-try accuracy | ≥ 85% | Opens without a "first/second/third" follow-up or retry |
| Daily use | ≥ 20 commands per day | Command log |

## Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Video audio triggers the wake word or garbles commands | False actions, missed commands | Uncommon wake word, noise suppression, briefly duck system volume while listening |
| "Pause" and "continue" share one toggle key, so saying "pause" on paused media resumes it | Wrong action | Read playback state via Windows media session API (GSMTC) and only send the key when state differs |
| Accent or pronunciation lowers STT accuracy | Low success rate | Test faster-whisper sizes; allow custom aliases per command |
| Many files share similar names | Wrong file opens | Rank by recency, ask with top 3, learn from choices |
| Media keys ignored by some apps or unfocused browser tabs | Command does nothing | GSMTC controls per session; app-specific fallbacks |
| Whisper on CPU too slow | Feels laggy | Use smaller model or Vosk for short commands; GPU if available |
| LLM API cost or outage | Loose commands fail | Local-first routing; LLM only as fallback; optional local model via Ollama |
| Always-on mic raises privacy concerns | Trust | All audio on-device, mic-active tray indicator, one-click mute |

## Milestones and roadmap

The working voice demo lands in week 1; full v1 is in daily use after about 5 to 6 weeks of part-time work.

&#91;embedded content: D3 roadmap · 4 phases, each with an exit gate\]

Each phase ends at its gate (the diamond and criteria below it); the next phase starts only when the gate passes.

## Open questions

- [ ] Wake word: "Hey D3" or a custom phrase? (needs training a custom openWakeWord model)
- [ ] Voice replies: spoken (TTS) or on-screen toast only?
- [ ] LLM fallback: Claude API or a local model via Ollama?
- [ ] Which folders and drives to index for file search?
- [ ] Personal tool only, or a future Lumora product?
- [ ] Is Sinhala command support needed later?
