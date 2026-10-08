# D3 — Build Plan

Source: `D3 — Voice-Controlled Desktop Assistant PRD.md` (Oct 8, 2026).
Status: planning. Decisions marked **[DECIDE]** are open (see the last section).

---

## 1. PRD analysis: what matters and what's missing

**The core of v1 is two loops:**
1. *Media loop:* "pause" / "continue" / "next" / volume. Must be instant and exact.
2. *File loop:* "open the StoreX proposal": search, rank, open, or ask "which one?"

Everything else (LLM fallback, tray settings, packaging) is support around those two loops.

**Gaps and conflicts found in the PRD**

| # | Issue | Resolution in this plan |
|---|---|---|
| 1 | **Latency conflict.** FR-3 ends capture after 1 s of silence, but the NFR wants < 1 s from end of speech to action. The silence wait alone uses the whole budget. | Use Silero VAD for endpointing with ~500–600 ms trailing silence. Measure latency from the VAD "speech end" timestamp. Log each stage separately (endpoint, STT, route, execute). |
| 2 | **Python version.** This PC has Python 3.14. ML wheels (onnxruntime, ctranslate2, openWakeWord) often lag behind new Python versions. | Pin **Python 3.12** through `uv` (already installed). |
| 3 | **No NVIDIA GPU** (Ryzen 7 5700G with Radeon iGPU). faster-whisper/CTranslate2 only accelerates on CUDA. | CPU int8 inference. Start with `base.en`, benchmark `tiny.en` and `small.en`. Pass a short `initial_prompt` with command vocabulary to bias recognition. |
| 4 | **"Hey D3" has no pretrained openWakeWord model.** | Phase 1 uses the pretrained `hey_jarvis` model plus push-to-talk. Train a custom "Hey D3" model in Phase 3 (openWakeWord synthetic-data notebook). |
| 5 | **Everything (voidtools) isn't installed.** The SDK needs the Everything service running. | Install Everything (Phase 0). Fallback: our own `os.scandir` index in SQLite for the configured folders. |
| 6 | **`winsdk` is deprecated.** | Use its successor, `pywinrt` (`winrt-Windows.Media.Control` and related packages). |
| 7 | **Pause/continue share one toggle key** (PRD risk). | GSMTC is the primary path: read `playback_status` and call `try_pause_async()` / `try_play_async()` directly. Media keys are only the fallback. |
| 8 | **"Open X" is ambiguous** (file, folder or app?). | Fixed resolution order: folder alias → installed app → file search. The LLM is only used when the phrasing doesn't fit `open/show/launch <x>`. |
| 9 | **"first/second/third" follow-up.** The PRD doesn't say whether the wake word is needed again. | After a disambiguation prompt, open a 6 s follow-up window that needs no wake word. |
| 10 | **Whisper hallucinates on noise** (e.g. "Thank you." on silence). Conflicts with FR-14. | Reject when `no_speech_prob` is high, `avg_logprob` is low, or the text is on a known-hallucination list. Then say "I didn't catch that." |
| 11 | **"play" vs "play <something>".** | Local media intents only match when the **whole utterance** is the command (after stripping filler words like "please", "D3"). |
| 12 | Roadmap and architecture diagrams are embeds that didn't export to the `.md`. | The phases are reconstructed below (4 phases, each with an exit gate). |

---

## 2. Architecture

```
 ┌──────────── audio thread ─────────────┐
 mic ─► sounddevice 16 kHz mono ─► ring buffer (~2 s pre-roll)
                                   │
                         openWakeWord (80 ms frames)     ◄── or push-to-talk hotkey
                                   │ wake
                          chime + duck volume
                                   │
                    Silero VAD endpointing (≈600 ms silence, 8 s max)
                                   │ utterance (numpy)
 ┌──────────── pipeline worker ──────────┐
       Vosk closed grammar (~30 ms) ── whole command phrase ──► keyword router
                                   │ no match
            faster-whisper tiny.en (CPU int8, ~280 ms) ─► text + confidence
                                   │
              confidence gate ── low ──► "I didn't catch that"
                                   │
              keyword router (regex + rapidfuzz)
                 │ match                      │ no match
                 │                       LLM router (tool use, text only)
                 ▼                            ▼
                         Intent(name, args)
                                   │
                           executor ─► handlers: media · volume · files · apps · dialog
                                   │
                feedback (TTS / toast / chime)  +  JSONL command log
 └───────────────────────────────────────┘
 tray icon (pystray, main thread): mic on/off indicator, mute, settings, quit
```

**Key design choices**
- **One `Intent` contract.** The keyword router and the LLM router both produce `Intent(name, args, source, confidence)`. The executor never knows which router produced it, so the LLM only needs a tool schema that mirrors the intents.
- **Dialog state machine.** States: `IDLE → LISTENING → THINKING → (AWAITING_CHOICE) → IDLE`. Disambiguation results are kept in `AWAITING_CHOICE` with a timeout. "cancel" works from any state.
- **Threads, not asyncio, at the edges.** The audio callback pushes frames into a queue. One worker thread runs wake → VAD → STT → route → execute. TTS gets its own thread (pyttsx3 isn't thread-safe). The tray runs on the main thread. GSMTC async calls run on a small dedicated asyncio loop.
- **Models load once at startup** and stay warm. The first whisper call is a warm-up on silence so the first real command isn't slow.

### Project layout

```
D3/
  pyproject.toml            # uv-managed, Python 3.12
  config.default.toml       # wake word, mic, voice, indexed folders, aliases, LLM
  src/d3/
    __main__.py             # entry: tray + pipeline
    app.py                  # orchestrator + dialog state machine
    config.py
    intents.py              # Intent dataclass, intent names, tool schema
    audio/
      capture.py            # sounddevice stream, device-loss recovery
      wake.py               # openWakeWord wrapper + push-to-talk
      vad.py                # Silero VAD endpointing
    stt/
      transcriber.py        # faster-whisper + confidence gate
    router/
      keyword.py            # regex + rapidfuzz phrase table
      llm.py                # Claude tool-use (or Ollama) fallback
    handlers/
      media.py              # GSMTC sessions + media-key fallback
      volume.py             # pycaw
      files.py              # Everything SDK search + ranking
      apps.py               # Start Menu .lnk + AppX index, launch/focus
      folders.py            # known-folder aliases (Downloads, Desktop, …)
    feedback/
      speaker.py            # pyttsx3 worker thread / toast
      sounds.py             # chimes (wake, ok, error)
      tray.py               # pystray icon + menu
    log.py                  # JSONL command log, per-stage timings
  assets/                   # chimes, icons, custom wake-word model
  tests/                    # router tests, ranking tests, recorded-audio fixtures
  scripts/
    bench_stt.py            # latency/accuracy across whisper sizes
    metrics.py              # success rate, latency, false wakes from the log
```

### Libraries (pinned stack)

| Concern | Package | Notes |
|---|---|---|
| Audio | `sounddevice`, `numpy` | |
| Wake word | `openwakeword` (ONNX on Windows) | `hey_jarvis` until the custom model exists |
| VAD | `silero-vad` (ONNX) or openWakeWord's built-in VAD | |
| STT, short commands | `vosk` + `vosk-model-small-en-us-0.15` (68 MB, in `models/`) | Closed grammar, ~30 ms |
| STT, open commands | `faster-whisper` `tiny.en` | CPU, `compute_type="int8"`, 8 threads, beam 5 |
| Router | `rapidfuzz` | |
| LLM | `anthropic` (Claude Haiku 4.5 for speed) **[DECIDE]** | Text only, tool use, ~2 s timeout |
| File search | Everything SDK via `ctypes` (`Everything64.dll`) | |
| Media | `winrt-Windows.Media.Control`, `winrt-runtime` | GSMTC |
| Media keys | `ctypes` `SendInput` (VK_MEDIA_PLAY_PAUSE etc.) | Fallback |
| Volume | `pycaw`, `comtypes` | |
| Hotkey | `keyboard` or `pynput` | Push-to-talk |
| TTS | `pyttsx3` (SAPI5) | |
| Tray | `pystray`, `Pillow` | |
| Toasts | `win11toast` or `winrt` notifications | If replies are on-screen |
| Packaging | `PyInstaller` | Plus HKCU `Run` registry key for autostart |
| Tests | `pytest` | |

---

## 3. Phases (each ends at a gate; the next phase starts only when the gate passes)

### Phase 0: Setup (½ day)
- [x] `git init`, `.gitignore`, `uv init` with Python 3.12, add dependencies
- [x] Install Everything 1.4 (service mode) and copy `Everything64.dll` to `vendor/` (works, ~40 ms per query once the DB has loaded)
- [x] Confirm mic device list and 16 kHz capture works (default: UGREEN webcam mic)
- [x] `scripts/bench_stt.py`: benchmark `tiny.en` / `base.en` / `small.en` on synthetic clips
- [x] Re-run the benchmark on the user's own voice (`scripts/record_commands.py`); added Vosk closed-grammar benchmark (`scripts/bench_vosk.py`)

**Gate 0:** whisper `base.en` transcribes a 2 s command in under 400 ms on this CPU (otherwise drop to `tiny.en`, or use Vosk for the media fast path).

**Gate 0 results (2026-10-08, synthetic SAPI voices, 40 clips, CPU int8, beam 1, warm model):**

| Model | Threads | Median | p90 | Exact |
|---|---|---|---|---|
| tiny.en | 8 | **257 ms** | 302 ms | 100% |
| base.en | 8 | 483 ms | 512 ms | 100% |
| small.en | 8 | 1522 ms | 1579 ms | 100% |

Latency is flat regardless of clip length (Whisper always encodes a 30 s window), so these numbers hold for any short command. 8 threads is clearly faster than 4.

**Real voice (user's recordings, quiet room, 20 clips, 8 threads):**

| Engine | Median | p90 | Correct |
|---|---|---|---|
| Whisper tiny.en, beam 1 | 281 ms | 775 ms | 65% exact / 75% fuzzy |
| Whisper tiny.en, beam 5 | 274 ms | 751 ms | 80% / 90% |
| Whisper base.en, beam 5 | 590 ms | 807 ms | 75% / 85% |
| **Vosk small-en, closed grammar** | **32 ms** | — | **16/16 short commands; 4/4 "open …" correctly rejected** |

Whisper fails on isolated single words ("resume" → "I see you", "quieter" → "Quiet."), and its long p90 tail comes from repetition ("previous, previous,"). All of Whisper's misses were one-word commands; it got every "open …" command right.

**Decision (Gate 0 passed): two-tier speech-to-text.**
1. **Vosk with a closed grammar** of every catalogue phrase (+ `[unk]`) runs first, ~30 ms. If the whole result is one command phrase → route locally, no Whisper.
2. Otherwise → **Whisper `tiny.en`, beam 5, 8 threads**, `max_new_tokens` capped (~24) to cut the repetition tail. This handles open commands ("open the StoreX proposal").

Vosk vocabulary gaps are handled by aliases (e.g. "unmute" is heard as "on mute"). Vosk word confidences saturate at 1.0, so the confidence gate rejects little: false matches during video playback must be measured in Phase 3 (record a `video-playing` take). Estimated latency: media commands ≈ endpoint (~500–600 ms) + ~30 ms; open commands ≈ endpoint + ~300 ms + search.

### Phase 1: Voice demo — media and volume (week 1)
- [x] Audio capture (80 ms frames); push-to-talk hotkey `ctrl+alt+space` (FR-2)
- [x] openWakeWord with `hey_jarvis` as a stand-in (FR-1); wake chime (FR-13)
- [x] VAD endpointing with openWakeWord's bundled Silero VAD (FR-3, 600 ms rule)
- [x] Two-tier transcriber: Vosk closed grammar → Whisper `tiny.en` fallback; confidence gate and hallucination filter (FR-14)
- [x] Keyword router: pause/stop/hold on, continue/resume/play, next/skip, previous/go back, volume up/down/louder/quieter, mute/unmute, cancel (FR-4, FR-10)
- [x] Media handler: GSMTC, state-aware pause/play across all sessions, next/previous, media-key fallback (FR-9)
- [x] Volume handler: ±10%, mute/unmute (FR-11)
- [x] **Moved up from Phase 3:** duck system volume to 20% while listening. Without it, a playing video keeps the VAD "hearing speech" and the command never ends.
- [x] JSONL command log with per-stage timings (FR-16), in `logs/`
- [x] Router unit tests (36 cases, including "play some music" must *not* resume)
- [x] `d3 --text "<command>"` runs a typed command without the mic, for testing handlers

Verified without a live speaker: all 20 of the user's recordings route to the right intent through the real transcriber.

**Live test (2026-10-08):** every command worked in Chrome (YouTube) and Spotify; latency 600–950 ms from end of speech. Two bugs found and fixed:
- *Clipped word onsets.* VAD fires ~100 ms after a word starts, so capture began mid-word. Vosk then missed it and Whisper hallucinated ("Have a great video", "Good job"). Simulated on the recordings: 11/16 without pre-roll, 16/16 with 160–240 ms. Fix: `preroll_ms = 240`.
- *Hotkey double-fire* caused a phantom listen after every hotkey command. Fix: trigger on key release and clear presses made mid-command. Hotkey changed to `` ` `` at the user's request.
- Added opt-in `log.save_audio` to keep captured clips in `recordings/debug/` for diagnosing misses (off by default). Idle while listening: 0.3% total CPU, 419 MB RAM, 1.7 s startup. On-screen toasts are deferred to Phase 2 (with spoken confirmations); Phase 1 gives chimes only.

**Gate 1:** pause/continue/next/volume work 10/10 times on YouTube in Chrome, on VLC and on Spotify. Saying "pause" on already-paused media does nothing. Median local latency < 1 s in the log.

### Phase 2: Open files, folders and apps (weeks 2–3)
- [x] Folder aliases: Desktop, Documents, Downloads, Pictures, Videos, Music via the Known Folder API (follows OneDrive redirection), "<letter> drive", and user aliases in `[folders.aliases]` (`projects` = `D:\PROJECTS`)
- [x] App index from `Get-StartApps` (172 apps, cached 24 h in `data/apps.json`), aliases ("VS Code" → Visual Studio Code), App Paths fallback for Chrome/Edge/Firefox. If the app already has a window it is focused instead of launched again (FR-12)
- [x] File search through Everything, scoped to `[search].roots`, with `[search].excludes` for dev junk and system folders (FR-6)
- [x] Ranking (FR-7): fuzzy per-word coverage of the name (misheard "proposive" still counts), parent-folder words at 0.8 weight ("the D3 plan" → `D3\PLAN.md`), precision, recency, type prior (documents up; code −12; junk −25; anything under `src\`/`main\`/`test\`/dot-folders −15), learned choices (+20), Windows Recent items (+≤10)
- [x] Date phrases ("last month's", "yesterday's", "this week's"...) are a +20 preference, not a hard filter: a hard `dm:` filter returned only a `.sql` file because no invoice was modified last month
- [x] Disambiguation: when the runner-up is within `ask_margin` (8), show a clickable "Which one?" list of the top 3 for 30 s; click a row or say "first/second/third" (or one/two/three) as a normal command (FR-8). **Changed after user test:** no spoken question and no automatic listening afterwards. The auto-listen mostly captured junk ("Thank you", "Enjoy").
- [x] Confirmations: chime + on-screen box (bottom right, never takes focus). No TTS anywhere (FR-13)
- [x] **After user test, folder names misheard** ("Lumora" → "Loumora" / "do more"; "D" → "D for a day"):
  - Whisper gets the user's folder and app names as a prompt hint. On synthetic clips tiny.en went from "Lumura" every time to 16/16 correct, beating base.en.
  - If nothing matches, words are spell-corrected against folder/app vocabulary ("loumora" → "lumora") and retried.
  - A lone letter = drive ("open D"); "image"/"photo" = Pictures
- [x] `scripts/try_open.py` (score breakdowns) and `scripts/eval_open.py` + `bench/open_cases.txt` for Gate 2
- [ ] Gate 2: user fills `bench/open_cases.txt` with ~20 real phrases; live test of the "which one?" dialog by voice

**Gate 2:** a test set of 20 real "open …" phrases from the user's own files gets ≥ 85% right first try; disambiguation works end to end.

### Phase 3: Robustness, LLM fallback and the real wake word (week 4)
- [ ] LLM router: Claude tool use with tools that mirror the intents; text only; 2.5 s timeout; on failure say "I didn't catch that" (FR-5)
- [ ] Train the custom "Hey D3" openWakeWord model; tune the threshold against 1 h of video playback (target < 1 false wake per hour)
- [ ] Duck system volume while listening and restore afterwards
- [ ] Mic unplug/replug recovery (re-open the stream when the device list changes)
- [ ] Custom aliases per command (from config) for accent issues
- [ ] `scripts/metrics.py`: success rate, median latency, false wakes, first-try file accuracy

**Gate 3:** 1 hour of YouTube playback gives < 1 false wake. Loose phrasing ("can you bring up the thing I sent to Kamal") resolves via the LLM in < 3 s. Unplugging and replugging the mic recovers without a restart.

### Phase 4: Tray, packaging and daily use (weeks 5–6)
- [ ] Tray: mic-active indicator, one-click mute, settings (wake word, mic, voice, indexed folders) (FR-15)
- [ ] PyInstaller one-folder build, autostart via HKCU `Run` key
- [ ] Idle resource check: < 5% CPU and < 500 MB RAM while listening
- [ ] 1 week of daily use, then review against the success metrics

**Gate 4 (v1 done):** ≥ 95% command success, median local latency < 1 s, ≥ 20 commands per day in the log.

---

## 4. LLM cost budget ($5 of API credit)

The LLM is only the fallback. Media, volume, folder, app and well-phrased "open …" commands never call it.

**One fallback call** (one request, no second turn: the LLM returns an intent and D3 runs it locally):

| Part | Tokens |
|---|---|
| System prompt + 6–7 tool definitions + tool-use overhead | ~1,000 in |
| The transcribed command | ~20 in |
| Tool call in the reply | ~60–100 out |

| Model | Cost per call | Calls per $5 |
|---|---|---|
| Claude Haiku 4.5 ($1 / $5 per MTok) | ≈ $0.0015 | ≈ 3,300 |
| Claude Opus 5.5 ($4 / $20 per MTok, thinking always on) | ≈ $0.007+ | ≈ 700 |

**Expected usage with Haiku 4.5:** ~20 commands/day, of which maybe 3–6 reach the LLM ≈ **$0.25/month**. Worst case, all 20/day go to the LLM ≈ $0.90/month. Phase 3 development and testing ≈ 300–500 calls ≈ $0.50–0.75. Phases 0–2 spend nothing.

Prompt caching won't help here: the prompt (~1K tokens) is below Haiku's minimum cacheable size. At this size that doesn't matter.

**Guardrails (built in Phase 3):**
- Set a monthly spend limit in the Anthropic Console.
- `llm.daily_call_cap` in config (default 50). Above it, D3 says "I didn't catch that" instead of calling.
- Log `usage.input_tokens` / `output_tokens` for every call; `scripts/metrics.py` reports spend to date.
- `max_tokens` 300, a short timeout and no retries loop, so one bad call can't run up cost.

---

## 5. Testing strategy
- **Router:** table-driven pytest (phrase → expected intent), including near-misses ("play some music" must *not* map to resume).
- **Ranking:** a fixture of fake file lists with expected top results.
- **Audio:** a folder of recorded WAV commands (mine, with background video audio) replayed through wake → VAD → STT to track accuracy as models change.
- **Handlers:** manual checklist per player (Chrome, Edge, VLC, Spotify, Windows Media Player).
- **Metrics:** everything measurable comes from the JSONL log, so the success metrics need no extra instrumentation.

---

## 6. Open questions — recommended defaults

| PRD question | Recommendation |
|---|---|
| Wake word "Hey D3" or a custom phrase | Keep "Hey D3", but train it in Phase 3. Use `hey_jarvis` + push-to-talk until then. |
| Spoken replies or toast only | Short spoken replies for questions ("Which one: 1… 2… 3…"); chime + toast for simple actions (faster, less intrusive while a video plays). |
| Claude API or Ollama | Claude API (Haiku 4.5): Ollama isn't installed, and a local LLM on CPU would miss the 3 s target. Keep the LLM client behind an interface so Ollama can be added later. |
| Folders and drives to index | **Decided:** Desktop, Documents, Downloads, Pictures (`C:\Users\User\Pictures` and `OneDrive\Pictures`), and **all of `D:\`**. Always excluded: `$RECYCLE.BIN`, `System Volume Information`, `Config.Msi`, `node_modules`, `.git`, `.venv`, `__pycache__`, `build`, `dist`, `.next`, `target`. The game folders on D:\ (Steam, Riot Games, VALORANT, Battle.net) hold thousands of asset files. Watch the ranking in Phase 2 and add them to the exclude list if they pollute results. |
| Personal tool or Lumora product | Build as a personal tool, but keep config, logs and paths out of code so it can be packaged later. |
| Sinhala later | Out of v1. faster-whisper's multilingual models support Sinhala poorly; revisit with a dedicated model if needed. |
