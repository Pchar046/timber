# Timber — brief for Claude Code

Read ARCHITECTURE.md first. It is the plan. This file is the rules.

## Project
Solo entry by Patrick Charles, Meta Global AI Developer Hackathon. Track: Indie Developer · Build for Your World (with a Push the Frontier story).
**Deadline: Wed Nov 4, 2026, 9:00 PM ET.** Submit: public GitHub repo + 3-minute demo video showing only work built Oct 26 – Nov 4.

Timber: a no-touch, behind-the-ear second brain for autistic adults and people living with dementia. Patrick is autistic and loses speech when overstimulated; he has watched elders live with dementia.

## Hard rules
- Meta's models do the heavy lifting (see ARCHITECTURE.md "Meta models").
- **No medical advice.** Never diagnose, name conditions, or suggest treatment. Daily-living support only.
- Facts Timber states must come from real records (proof-carrying speech). Never guess what the person thinks.
- Keep `.env` out of git. Repo will be public.

## The core idea: the Calm-Self Proxy
Agents usually need the person present to approve actions. Timber's person can't at the critical moment. So:
1. Calm-Self Contract — rules the person writes while calm (may say / never say / who to contact / what helps). It stands in for confirmation.
2. Forecast, then act — predict the next few minutes, simulate options, pick the gentlest that works.
3. World as tools — nearby devices register as tools; the Mind finds them with tool search.
4. Proof-carrying speech — every factual sentence carries a receipt and is verified before it's spoken.
5. Sleep-time learning — nightly Muse Wonder review proposes playbook changes; the calm self approves them.

## Patrick's design decisions (Nov 2)
- First sign of overload → alert on his phone AND notify his trusted person, right as it starts.
- When speech switches off → Timber speaks on its own from his personal data, and says it is Timber speaking.
- Haptics first: a gentle vibration, not a voice or sound.
- Care circle (family, partners) sees episodes per day and mood during them, to support and understand him.
- Two modes that feel different: Autism mode and Dementia mode, each personalized.
- Dementia: remember what they said earlier, understand what's happening, stay connected to people and events, daily brain activities.
- Trust: help without hurting comfort, and show improvement over time.

## Meta Model API facts (from dev.meta.ai docs)
- Base `https://api.meta.ai/v1`, header `Authorization: Bearer $MODEL_API_KEY`, OpenAI-compatible (chat completions + responses).
- Muse Spark: `muse-spark-1.3`. Tool calling, images, 1M context.
- Muse Wonder: `muse-wonder-1.0`. Bigger, slower, pricier ($4/$20 per 1M) — nightly only.
- SAM 3.1: `sam-3.1` via `POST /v1/responses`; content = `input_text` (short noun phrase) + `input_image` (URL or data URI). Output has `<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|>` records. Metadata `include_confidence: "true"` (string).
- Muse Voice Transcribe: `muse-voice-transcribe-1.0`. Realtime `wss://api.meta.ai/v1/asr/realtime`. First frame: `{"authorization":{"accessToken":"Bearer <key>"},"audioEncoding":"PCM_24KHZ","model":"muse-voice-transcribe-1.0","mode":"DIARIZATION"}`; wait for `{"sessionId"}`; send raw 16-bit LE mono PCM binary; events `transcript` (cumulative partial — replace, don't append), `speaker`, `speechComplete`; finish with `{"type":"endStream"}`. Key stays on the server — browser streams to our backend, which relays.
- Muse Image: generation + editing, ~18 s per image — pre-generate, never on the live path. Confirm the exact model ID in the dev.meta.ai model list before coding it.
- Every model call must have a demo-mode fallback when no key is set, and must be logged so the console can show "Meta models at work".

## Old project (reference only)
`C:\Users\PJcha\Downloads\timber\timber` has working pieces to copy, not import: `app/home.py` (Home Assistant + simulated home), Bluetooth heart-rate in `static/app.js`, the Meta client in `app/muse.py`, the memory graph in `app/agents/memory.py`.

## Running on Patrick's PC (Windows, Python 3.15)
- Use `py -m pip ...` and `py -m uvicorn backend.main:app --reload` (pip/uvicorn aren't on PATH).
- Don't use `uvicorn[standard]` — pyyaml won't build on 3.15. Use `uvicorn` + `websockets`.

## How to work with Patrick
- He's building step by step and wants control. Do ONE step at a time from the build order, then stop and tell him how to test it.
- Keep changes small. Explain what changed in a line or two, plain language.
- Ask before deleting or rewriting anything big.
