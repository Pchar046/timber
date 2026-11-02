# Timber: Architecture & Agent Flow

Nov 2, 2026

Timber is a no-touch, behind-the-ear second brain for autistic adults and people living with dementia. Meta's models are its mind: they hear, see, reason and act across every device around the person. Submit to **Indie Developer · Build for Your World**, with a clear "Push the Frontier" story.

## Track fit

| Track asks | How Timber answers |
| --- | --- |
| **Build for Your World:** solve a problem you know firsthand | Patrick is autistic and loses speech when overstimulated. He has watched elders live with dementia |
| Free up time and mental space | Timber watches for overload, remembers for you, and speaks for you, so the person doesn't have to manage anything |
| Make something possible that wasn't before | Someone who has lost their speech can still answer a cashier. Someone with dementia can ask "what did I say this morning?" and get a true answer |
| **Push the Frontier:** give a machine a new capability | A behind-the-ear wearable that predicts overload and acts before the person asks |
| Understand and control a physical system | Timber reads body signals and room conditions, then changes the room (lights, sound, temperature) to bring the person's load down |
| Show what's possible when the models meet the real world | Live heart rate, a real microphone and a real home, all running through Muse Spark in real time |

**Rules Timber follows:** the repo is public; the demo shows only what was built Oct 26 – Nov 4; and there is **no medical advice**. Timber supports daily living. It never diagnoses or suggests treatment.

## The new agent pattern: the Calm-Self Proxy

Every agent framework today assumes the person is there to instruct the agent and approve what it does. Muse Spark 1.3 itself is built to [ask clarifying questions and confirm before consequential actions](https://letsdatascience.com/news/meta-releases-muse-spark-13-for-agentic-work-cd8f3f11). Timber's person can't do that at the critical moment: speech is gone, or memory is gone. **So Timber's agents take instructions from the person's calm self, act for the person's overloaded or confused self, and prove every word they say.** We didn't find this pattern in any product or study we reviewed. That's the claim to make, worded carefully.

Five parts, each built on a real Meta capability:

1. **Calm-Self Contract.** While calm, the person writes the rules with Timber, in plain words: what Timber may say for them, what it must never say, who to contact, what helps, and what to avoid. When the person can't answer, the contract stands in for the confirmation step. *Meta: Muse Spark turns the conversation into a structured, checkable contract.*
2. **Forecast, then act.** The Mind doesn't wait for a threshold. It forecasts the next few minutes from your history, your calendar ("store at 3 PM") and the room. Before acting, it simulates the options ("if the lights dim now, does the forecast drop?") and picks the gentlest one that works. *Meta: Muse Spark reasoning over a long state history (1M-token context).*
3. **The world as tools.** Every device near the person registers itself as a tool. Walk into a new room or a store, and new tools appear; walk out, and they're gone. The Mind finds them with tool search instead of carrying a fixed list. This is your "technopathy" idea, made real. *Meta: Muse Spark tool calling, tool search and parallel calls.*
4. **Proof-carrying speech.** Anything Timber says as fact ("you said this at 9 AM", "your keys are on the counter") carries a receipt: the record, sensor or photo it came from. A second check verifies the sentence against its receipt before it's spoken, and anything unproven is dropped. When Timber speaks for you, the check is against your Calm-Self Contract. *Meta: Muse Spark drafts, a separate Muse Spark verifier checks (Muse Glimmer on-device later), Voice Transcribe and SAM 3.1 supply the receipts.*
5. **Sleep-time learning.** Each night, the Reflector treats the day's interventions as experiments: which ones ended episodes faster, and which ones you overrode. It proposes changes to tomorrow's playbook, and they take effect only after your calm self approves them in the morning. *Meta: Muse Wonder for the long nightly review; approval goes back through the contract.*

**One-line pitch:** "Agents usually need you present to guide them. Timber's agents work for you when you can't be: you instruct them while calm, they act on a forecast, prove every word, and learn overnight with your approval."

## Meta models: who does what

Each Meta model gets one clear job. Muse Spark is the fast mind that runs all day. Muse Wonder thinks deeply once a day. The rest are senses, hands and privacy. The first five are must-haves for Nov 4; the last three are stretch goals.

| Model | Role in Timber | Runs where | Why this model |
| --- | --- | --- | --- |
| [Muse Spark 1.3](https://dev.meta.ai/models/muse-spark) `muse-spark-1.3` | **The Mind** and the specialist agents: decides state, picks the next action, drafts what to say | Meta Model API | Fast and cheaper ($1.25 in / $4.25 out per 1M tokens), tool calling, reads images |
| [Muse Voice Transcribe](https://dev.meta.ai/models/muse-voice-transcribe) `muse-voice-transcribe-1.0` | **The Ears**: always listening. Diarization separates your voice from other people's | Meta Model API (realtime socket) | Live streaming, speaker labels, 25 languages, $0.18 per hour |
| [SAM 3.1](https://dev.meta.ai/models/sam-3-1) `sam-3.1` | **The Eyes**: finds objects in a photo or video ("where are my keys?") | Meta Model API | Text-prompted segmentation and video tracking |
| [Muse Wonder](https://www.llmreference.com/provider/meta-model-api/muse-wonder) `muse-wonder-1.0` | **The Reflector**: each night, reviews the whole day to update your personal baseline, write the care-circle summary, track progress and plan tomorrow | Meta Model API, batched once a day | Larger model for long-running agent work; takes text, image, video and audio; 1M-token context. Too costly to run all day ($4 in / $20 out), right for one deep pass |
| [Muse Image](https://openrouter.ai/meta/muse-image) | **The Illustrator**: personal picture cards for brain exercises, visual daily schedules, calm cards, consistent with family reference photos | Meta Model API, generated ahead of time | Generates and edits, keeps subjects consistent, renders text well. About 18 s per image, so never on the live path |
| [Muse Glimmer](https://github.com/meta-models/meta-oss-cookbook) (open weights) | **The Guardian on the device** *(stretch)*: runs privately on a home hub or laptop, filters sensitive audio before anything goes to the cloud, and keeps the core loop working offline | Local GPU, offline | Built to run local agents on a single GPU, with no API key and nothing leaving the machine |
| [Muse Spark Open](https://dev.meta.ai/models/muse-spark-open) (open weights) | **Your Voice** *(stretch)*: fine-tuned on your own phrases, so speak-for-me sounds like you | Baseten `meta-models/muse-spark-open-1026` or Fireworks | Open weights you can train on your own data |
| [Muse Code](https://dev.meta.ai/lp/muse-code) | **The Builder**: coding agent used to build Timber, alongside VS Code | Your machine | Name it in the submission as part of how you built |

## System architecture

*(Diagram: Timber architecture · devices to outputs. See the Claude Doc.)*

Read it top to bottom. Every device feeds one gateway, which turns each input into an event. The Ears and Eyes turn sound and images into events too. The Mind reads your live state, your profile and your memory, makes one decision, and hands it to the specialist agent that carries it out.

## The agents

One Mind, seven specialists. The person only ever meets "Timber." Underneath, the Mind hands each moment to the right specialist. Each agent is a Muse Spark call with its own instructions and tools, except the Reflector, which runs on Muse Wonder.

| Agent | Job | Wakes up when | Meta model | Tools it can use | Output |
| --- | --- | --- | --- | --- | --- |
| **Mind** (orchestrator) | Decides what happens next, one step at a time, using the escalation ladder | Any event: a reading, speech heard, a routine time, a gesture, plus a check every 5 s | Muse Spark | Calls every other agent | One decision per event, logged with its reason |
| **Sense** | Reads load against *your* baseline: calm, rising or overloaded. Tracks energy and whether you can speak | New watch, mic or motion data | Muse Spark + a rule score as a safety check | Read signals, read baseline | State + confidence + reason |
| **Voice** | Speaks for you when speech switches off, and fills in a word you can't reach | Speech heard while you're quiet or overloaded; a pause mid-sentence | Voice Transcribe (diarized) + Muse Spark | Your profile (needs, phrases), recent conversation | Spoken reply, which starts with "This is Timber, speaking for Patrick" so people know |
| **Memory** | Saves what really happened and answers "what did I say? where did I put it? who is this?" | Anything said, done or seen | Muse Spark + SAM 3.1 | Memory graph, camera photos | Cited answers; records only from real sources |
| **Coach** | Dementia: brain exercises, spaced retrieval and picture reminiscence. Autism: shows your progress | Scheduled practice times; after episodes | Muse Spark + Muse Image | Memory graph, picture cards | Exercises, picture cards, progress notes |
| **Environment** | Changes the room to lower the load | The Mind escalates past a vibration | Muse Spark (tool calls) | Home gateway: lights, speaker, thermostat | Device actions, confirmed |
| **Guardian** | Alerts the care circle, enforces what may be shared, and blocks any medical advice in outgoing text | Rising or overloaded state; every outgoing message | Muse Spark (Muse Glimmer on-device later) | Care-circle list and permissions | Phone alerts, checked messages |
| **Reflector** | Nightly: reviews the day, updates your baseline, writes the care-circle summary and progress, plans tomorrow | Once a night | **Muse Wonder** | The whole day's state history, episodes and memory | Updated baseline, daily summary, trends |

**Two modes, one Mind.** The mode sets the tone, which agents lead, and which outputs are allowed.

|  | Autism mode | Dementia mode |
| --- | --- | --- |
| Leads | Sense, Voice, Environment, Guardian | Memory, Coach, Sense |
| First touch | A gentle vibration (no voice unless you choose it) | A calm voice in the ear, in a familiar voice |
| Speaks for you | Yes, on its own when speech switches off | Helps find words; reminds you what you said earlier |
| Care circle sees | Episodes per day, mood around each, trends | Orientation, routines done or missed, wellbeing, trends |
| Exercises | Progress view over time | Daily brain activities and reminiscence |

## Agent flow: Autism mode

*(Diagram: Autism mode · escalation ladder, 3 steps. See the Claude Doc.)*

The Mind always uses the least intrusive help first, matching your answers: a gentle vibration, never a voice in your ear. You and your trusted person are told as soon as it starts. Timber speaks out loud only when your speech has switched off, and it always says it is Timber talking. Every episode is saved with its mood, so the care circle and the nightly Reflector can see patterns over time.

## Agent flow: Dementia mode

In Dementia mode, the Mind's main job is keeping the person oriented and connected through the day. Four loops run.

**1. Orientation (morning, and whenever they seem lost)**

1. Trigger: they wake up, or Sense notices confusion (wandering movement, repeated questions heard by the Ears).
2. Memory pulls today's facts from real records: the day and date, where they are, who's coming, what's next.
3. The Mind speaks one short line in a familiar voice: "Good morning. It's Monday, November 2nd. You're home. Maria visits at 6."
4. The same words are used every day (the cat rule: consistency).

**2. Conversation memory ("what did I say earlier?")**

1. The Ears transcribe all day. Diarization tags who said what.
2. Memory saves key moments as records: who, what, where, when, source = heard.
3. Asked later, Memory answers only from those records and says when it was said.

**3. Staying connected to people and events**

1. A face or a voice the Mind knows comes up (from a photo, the camera, or a name heard).
2. Memory links it in the graph: person, relationship, last visit, shared events.
3. The Mind whispers a prompt: "This is your daughter Maria. She brought sunflowers on Saturday."

**4. Brain activities (daily)**

1. Coach picks two or three short activities from the person's own life: spaced-retrieval questions, "who's in this photo?", or what they did yesterday.
2. Muse Image makes picture cards ahead of time (family and familiar places, consistent with reference photos).
3. The answer is shown first the first time (errorless learning), then the gaps grow longer.
4. Results go to the Reflector, which tracks progress and adjusts tomorrow's activities.

**Every night, the Reflector (Muse Wonder)** reviews the day: orientation moments, routines done or missed, exercise results and overload moments. It updates the baseline and writes the care-circle summary.

## Project structure and build order

Start a fresh folder, and keep the old `timber` folder as a reference. It already has pieces worth copying over: the Home Assistant gateway (`home.py`), Bluetooth heart rate in the browser, the Meta client and the memory graph.

```
timber/
  CLAUDE.md            brief for Claude Code (rules, models, decisions)
  ARCHITECTURE.md      this document, exported
  backend/
    main.py            FastAPI app, REST + websockets
    meta/              one client per Meta model: spark.py, wonder.py, image.py, sam.py, transcribe.py
    core/
      events.py        event bus: every input becomes an event
      state.py         live personal state + baseline
      memory.py        memory graph (people, places, objects, events, routines)
      profile.py       the person: mode, needs, phrases, care circle, permissions
      db.py            SQLite storage
    agents/            mind, sense, voice, memory, coach, environment, guardian, reflector
    devices/
      gateway.py       ingest from watch, phone and sensors
      home.py          Home Assistant or a simulated home
  apps/
    person/            phone app: vibration, alerts, progress (installable web app)
    circle/            care-circle view for family and partners
    console/           demo console for the video
  data/                seed people, places, routines, profile
```

**Three contracts that keep the agents clean**

- **Event**: `{type, source, data, t}`. Types: `reading`, `speech_heard`, `speech_complete`, `gesture`, `routine_due`, `photo`, `tick`.
- **Decision** (from the Mind): `{action, agent, level, reason, t}`. Actions: `vibrate`, `notify_phone`, `notify_circle`, `adjust_home`, `speak_for_you`, `whisper`, `remember`, `exercise`, `none`.
- **Profile**: `{mode, name, needs[], phrases[], circle[{name, relation, sees[]}], comfort{haptic_only, quiet_hours}}`.

**Build order (deadline Wed Nov 4, 9:00 PM ET)**

| When | Build | Done when |
| --- | --- | --- |
| Mon Nov 2, evening | Skeleton, event bus, Meta clients, profile, Sense agent, Mind with the ladder; phone app vibrates | A watch reading makes the phone vibrate, and the Mind logs why |
| Tue Nov 3, morning | Real API key test across all models. Guardian: care-circle alerts, episodes and mood. Environment: home actions | The trusted person gets an alert as load rises; the lights dim |
| Tue Nov 3, afternoon | Voice: diarized live listening + automatic speak-for-you from the profile | Someone asks a question while you're in shutdown, and Timber answers |
| Tue Nov 3, night | Reflector on Muse Wonder: daily summary + progress view | The care circle sees today's episodes and trend |
| Wed Nov 4, morning | Dementia mode: orientation, conversation memory, Coach with Muse Image cards | "What did I say this morning?" gets a cited answer |
| Wed Nov 4, by 5 PM | README, public repo, record and edit the video | Submitted with hours to spare |

If time runs short, cut from the bottom of the Dementia work, never the Autism-mode story. That's the one judges will remember.

## Demo story: 3 minutes

One real moment, no clicking. Every beat shows a Meta model doing the work, and the console's model log proves it on screen.

| Time | What happens | Meta model on screen |
| --- | --- | --- |
| 0:00–0:20 | Patrick on camera: "I'm autistic. When I'm overstimulated, I lose my words." | (the problem) |
| 0:20–0:50 | A loud store. His watch heart rate and the room noise climb. Timber decides the load is rising against *his* baseline, and the phone vibrates gently | Muse Spark (Sense + Mind) |
| 0:50–1:10 | His trusted person's phone gets an alert. At home, the lights dim | Muse Spark (Guardian, Environment) |
| 1:10–1:50 | A cashier asks a question. Patrick can't answer. Timber hears it and replies: "This is Timber, speaking for Patrick…" | Voice Transcribe (diarized) + Muse Spark (Voice) |
| 1:50–2:20 | Dementia mode: "What did I say this morning?" gets a cited answer, then "Where are my keys?" from a photo | Muse Spark (Memory) + SAM 3.1 |
| 2:20–2:40 | Tonight's summary: the care circle sees today's episodes, mood and trend. A brain-exercise picture card for tomorrow | Muse Wonder (Reflector) + Muse Image |
| 2:40–3:00 | The architecture slide and the line: "Timber gives people back the moments their brain drops." | All |

Before recording, label anything that is simulated (for example, the cashier is a friend, or the home is simulated). Judges reward honesty, and the rules require showing only what was built during the hackathon.
