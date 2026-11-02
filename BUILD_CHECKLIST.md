# Timber build checklist

Do the steps in order. For each one:
1. Paste the **Prompt** into Claude Code.
2. Run the **Test**.
3. Tick the box only when the test passes. If it fails, paste the error into Claude Code and say "fix this, keep the change small."

Commit after every passing step, so you can always go back:
```
git add -A
git commit -m "Step N done"
```

Deadline: **Wed Nov 4, 9:00 PM ET.** Aim to submit by 5 PM.

---

## Monday Nov 2, evening: the core loop

- [ ] **Step 1. Skeleton** *(you already did this)*
  - **Prompt:** Read CLAUDE.md and ARCHITECTURE.md. Create the folder skeleton, requirements.txt, .gitignore (include .env), and backend/main.py with a /health route. Tell me how to run it and stop.
  - **Test:** http://127.0.0.1:8000/health shows a response.

- [ ] **Step 2. Git + GitHub**
  - **Prompt:** Initialize git in this folder and make the first commit. Then tell me the exact steps to create a PUBLIC GitHub repo called timber and push to it. Make sure .env is not committed.
  - **Test:** The repo is on github.com, and `.env` is NOT in it.

- [ ] **Step 3. Meta model clients**
  - **Prompt:** Build backend/meta/: spark.py (Muse Spark chat, JSON output, images), sam.py (SAM 3.1), transcribe.py (Voice Transcribe realtime relay), wonder.py (Muse Wonder), image.py (Muse Image). Use the facts in CLAUDE.md. Every call needs a demo-mode fallback when MODEL_API_KEY is missing, and must log {model, task, ms, status} to a shared model log. Add GET /api/models/log. Copy useful code from the old project's app/muse.py.
  - **Test:** /api/models/log works. With no key, calls return demo answers and show status "demo".

- [ ] **Step 4. Event bus + profile + state**
  - **Prompt:** Build backend/core/: events.py (event bus using the Event contract in ARCHITECTURE.md), profile.py (Profile contract, loaded from data/profile.json with my real decisions from CLAUDE.md: Autism mode, haptic first, trusted person, phrases), state.py (live signals plus a personal baseline learned from the first readings), db.py (SQLite). Add POST /api/events and GET /api/state.
  - **Test:** Post an event, and /api/state shows it.

- [ ] **Step 5. Device gateway**
  - **Prompt:** Build backend/devices/gateway.py: POST /api/devices/ingest accepts one reading or a list, maps names like hr/pulse/noise to heart_rate/noise_db/movement, ignores impossible values, and turns each reading into an event. Also copy the Bluetooth heart-rate watch connection from the old project's static/app.js into a test page.
  - **Test:** A curl reading shows up in /api/state. If you have a Bluetooth heart-rate device, it streams in live.

- [ ] **Step 6. Sense agent**
  - **Prompt:** Build agents/sense.py: Muse Spark decides calm/rising/overloaded against my baseline and the recent trend, returns {level, confidence, reason}. A rule score runs as a safety check (rules win if they disagree by 2 levels). Throttle Muse calls to at most every 15 s unless the rule level changes. No medical language.
  - **Test:** Post rising heart rate and noise. The level goes calm → rising → overloaded, with a reason.

- [ ] **Step 7. The Mind + escalation ladder**
  - **Prompt:** Build agents/mind.py: a background loop that reads each event and makes one Decision (contract in ARCHITECTURE.md), following the Autism mode ladder: rising → vibrate + notify_phone + notify_circle; still climbing → adjust_home; speech switched off → speak_for_you; calm again → save episode. Log every decision with its reason. Add GET /api/decisions.
  - **Test:** During a rising scenario, /api/decisions shows the ladder in order, each with a reason.

- [ ] **Step 8. Phone app (person)**
  - **Prompt:** Build apps/person/: a mobile web page served at /person. It shows my current state in calm colors, vibrates the phone gently (navigator.vibrate) on a vibrate decision, shows an alert on notify_phone, and has one big "I need a break" button. Make it work on my phone over the same Wi-Fi (tell me how to run uvicorn on 0.0.0.0 and which address to open).
  - **Test:** Open /person on your phone. Run the rising scenario and the phone vibrates.

---

## Tuesday Nov 3, morning: real keys + care circle + home

- [ ] **Step 9. Real API key test**
  - **Prompt:** I added MODEL_API_KEY to .env. Write scripts/test_models.py that calls each Meta model once (Spark, SAM on a sample image, Wonder, Image, a 3-second Transcribe test) and prints pass/fail with the error. Fix any errors.
  - **Test:** Every model prints PASS. (If Muse Image or Wonder fails on access, note it and move on.)

- [ ] **Step 10. Calm-Self Contract**
  - **Prompt:** Build the Calm-Self Contract: data/contract.json plus a page at /contract where I write, while calm: what Timber may say for me, what it must never say, who to contact, what helps, what to avoid. Muse Spark turns my plain words into the structured contract. Every agent action must check the contract first.
  - **Test:** Write a rule like "never tell strangers I'm autistic." The contract page shows it as a structured rule.

- [ ] **Step 11. Guardian agent + care circle**
  - **Prompt:** Build agents/guardian.py and apps/circle/ at /circle: when the state goes rising, notify my trusted person right away. Save each episode with its start, end, peak, what helped, and an estimated mood labeled "estimated". /circle shows episodes per day, mood, trend, and live alerts. Guardian checks every outgoing message for medical advice and for anything the contract forbids.
  - **Test:** Open /circle in a second browser. A rising scenario shows an alert there within seconds.

- [ ] **Step 12. Environment agent (world as tools)**
  - **Prompt:** Copy home.py from the old project into devices/home.py. Then make devices register themselves as tools (name, room, actions) and come and go. The Mind uses Muse Spark tool calling and picks tools by searching what's registered now, not from a fixed list. Show the home state on the console.
  - **Test:** On overload, the lights dim (simulated, or real with Home Assistant). Remove a device, and the Mind stops using it.

---

## Tuesday Nov 3, afternoon: the voice

- [ ] **Step 13. The Ears (live listening)**
  - **Prompt:** Build the live-listening relay: the browser streams 24 kHz PCM to /ws/listen, the backend relays to Muse Voice Transcribe in DIARIZATION mode, and transcript and speaker events become speech events on the bus. Let me tag which speaker label is me.
  - **Test:** Talk with another person, and the console shows who said what.

- [ ] **Step 14. Speak for me (automatic)**
  - **Prompt:** Build agents/voice.py: when I'm overloaded or have gone quiet and another speaker asks me something, Muse Spark drafts my reply from my profile and contract. Proof-carrying speech: a second Muse Spark call checks the draft against the contract and drops anything not allowed. Then it speaks out loud and starts with "This is Timber, speaking for Patrick."
  - **Test:** In overload, a friend asks "Do you need a bag?" and Timber answers out loud for you.

- [ ] **Step 15. Fill the blank**
  - **Prompt:** Add automatic word-fill: when I pause mid-sentence for about 2 seconds, Muse Spark predicts the word and the phone shows it (vibration plus text, no voice, unless I turn voice on).
  - **Test:** Say "can you pass the..." and stop. The word appears.

---

## Tuesday Nov 3, night: learning overnight

- [ ] **Step 16. Reflector (Muse Wonder)**
  - **Prompt:** Build agents/reflector.py: a "run reflection now" button plus a nightly schedule. Muse Wonder reviews the day's episodes, decisions, and what helped, writes the care-circle daily summary, updates my baseline, and PROPOSES playbook changes. Proposals wait in /contract for my approval before they apply.
  - **Test:** Run it. The summary appears on /circle, and a proposal waits on /contract.

- [ ] **Step 17. Progress view**
  - **Prompt:** Add a Progress section to /person: episodes per day over time, average episode length, and which helps worked best. Simple charts, calm colors.
  - **Test:** After a few scenarios, you see your trend.

---

## Wednesday Nov 4, morning: Dementia mode

- [ ] **Step 18. Mode switch**
  - **Prompt:** Add a mode switch (Autism / Dementia) in the profile. Each mode changes the tone, which agents lead, and the first touch (vibration vs calm voice), as in ARCHITECTURE.md.
  - **Test:** Switching modes changes how Timber responds.

- [ ] **Step 19. Memory: conversation + orientation**
  - **Prompt:** Build agents/memory.py using the old project's memory graph: save key moments heard (who, what, when, source). Answer "what did I say this morning?" only from records, with a receipt. Add morning orientation: the same words every day ("It's Monday, November 2nd. You're home. Maria visits at 6.").
  - **Test:** Say something, then ask what you said. You get a cited answer.

- [ ] **Step 20. Find it (SAM 3.1)**
  - **Prompt:** Add "Where are my keys?": take a photo, SAM 3.1 finds the object, Muse Spark says where in plain words, the box shows on the photo, and it's saved to memory with source "camera".
  - **Test:** Photograph a desk and ask for your keys. A box appears around them.

- [ ] **Step 21. Coach + Muse Image**
  - **Prompt:** Build agents/coach.py: two or three daily brain activities from the person's own memories (spaced retrieval, "who is in this photo?"). Muse Image pre-generates picture cards. Show the answer first the first time, then lengthen the gaps.
  - **Test:** An activity shows with a generated picture card.

---

## Wednesday Nov 4, by 5 PM: submit

- [ ] **Step 22. Demo console**
  - **Prompt:** Build apps/console/ at /console for the video: live state, the decision log with reasons, the "Meta models at work" log, home state, and a "play store scenario" button that runs the whole Autism-mode story with no clicks.
  - **Test:** One button plays the full story start to finish.

- [ ] **Step 23. README**
  - **Prompt:** Write README.md: what Timber is, the Calm-Self Proxy, which Meta model does what, the architecture, how to run it, "Built during the hackathon (Oct 26 – Nov 4)", what is simulated, and "no medical advice." Add the architecture diagram as an image or Mermaid.
  - **Test:** A stranger could run it from the README.

- [ ] **Step 24. Final check**
  - **Prompt:** Check the repo for secrets (API keys, tokens), remove debug code, make sure it runs from a fresh clone in demo mode, and list anything broken.
  - **Test:** Clone to a new folder, run it, and it works. No keys anywhere in GitHub.

- [ ] **Step 25. Record the video (3 minutes)**
  - Follow "Demo story" in ARCHITECTURE.md.
  - Show the model log on screen. Say clearly what's simulated.
  - Never show your API key or `.env` on screen.

- [ ] **Step 26. Submit**
  - Submit on the Cerebral Valley platform: public repo link, video link (set to public or unlisted), and description.
  - Open both links in a private browser window to make sure they work.

---

## If you fall behind
Cut in this order: Step 21 → 17 → 15 → 20 → 18–19. **Never cut Steps 6–8, 11, 14 or 22.** Those are the story judges remember.

## Quick fixes
- `pip`/`uvicorn` not found → use `py -m pip ...` / `py -m uvicorn ...`
- "No module named backend" → you're in the wrong folder; the terminal should end in `timber-v3>`
- Phone can't reach the app → run uvicorn with `--host 0.0.0.0`, use your PC's IP, same Wi-Fi, allow Python through Windows Firewall
- Vibration doesn't work → Android Chrome supports it; iPhone Safari doesn't. Use an on-screen pulse as the fallback
- Ran out of credits → check the Billing page; demo mode still works for building
