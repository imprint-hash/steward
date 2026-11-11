# Steward — the chief of staff for your team chats

**Every Friday, someone in the company spends hours chasing department reports in group chats, then
more hours turning them into a weekly update. Steward does both, inside the chats people already use.**

Steward is a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin. Each week it:

1. **Asks each department for its update in its own group:** Site Operations, Procurement, Safety
   and so on. The lead replies however is easiest: a **voice note in any language**, a few lines of
   text, a **photo** (a handwritten site diary, a whiteboard) or a **file** (PDF, Word, Excel).
2. **Reminds only the teams that haven't replied,** politely, never outside working hours.
3. **Gives the founder one weekly pack** in their private chat, plus a **60-second voice brief** to
   listen to on the way to work. The pack covers last week, *promised vs done*, a *stuck radar*,
   the numbers with week-on-week change, and next week's agenda.
4. **Posts the staff version to the all-staff group** once the founder replies **"send"**. The staff
   version shows only what got done. It leaves out the blockers, the free-text summaries, who was late,
   anything marked private and (unless you switch it on) the numbers.

Nobody installs an app, opens a dashboard or fills in a form. Each department lead answers in the
group they already use.

---

## Who it's for

- **Founders and managing directors of small and mid-sized companies** that run on group chats: one
  group per department, plus one for all staff. That's the normal setup for SMEs across Africa,
  Latin America, India and Southeast Asia.
- **Operations managers and chiefs of staff** whose Friday is spent chasing reports and writing
  the "last week / next week" message.
- **Field-heavy businesses** where reports arrive as voice notes and photos rather than documents:
  construction, logistics, manufacturing, agriculture, retail chains and clinics. Our worked example
  below is a construction company, because that's where this idea came from.
- **Department leads** who would rather talk for a minute than write a report.

### Where it runs
Steward never sends a message itself. It schedules everything through Hermes, so it works on every
platform the Hermes gateway connects to:

| Where companies talk | Hermes gateway platform |
|---|---|
| WhatsApp groups (most SMEs in Africa, LatAm, India) | `whatsapp`, `whatsapp_cloud` |
| Microsoft Teams (corporates) | `teams` |
| Google Chat (Google Workspace companies) | `google_chat` |
| Slack (tech and agencies) | `slack` |
| Telegram, Discord, Signal | `telegram`, `discord`, `signal` |
| LINE, WeCom, Feishu/Lark, DingTalk (Asia) | `line`, `wecom`, `feishu`, `dingtalk` |
| Email, SMS | `email`, `sms` |

A company can mix them. For example, Site Operations can be on WhatsApp while the founder reads the
pack on Telegram.

### Every team in its own language
- **Each department can have its own language.** The Finance check-in can go out in French, the
  Safety one in Hindi and the Procurement one in Chinese or Spanish.
- **People answer in whatever they speak:** French, Hindi or Hinglish, Mandarin or Cantonese,
  Spanish, Portuguese, Arabic, Swahili, Pidgin, Yoruba or Hausa. Steward replies **in their own
  language** and reads back what it understood.
- **The founder chooses the language of the pack and the voice brief:** English, French, Chinese
  or whatever they're used to. The all-staff update follows the founder's choice, or it can have
  its own language.
- Reports are always stored in plain English, so promised vs done and the stuck radar work across
  languages. Translation keeps every name, number and date unchanged.

---

## Impact

The weekly report is one of the most common recurring chores in any company, and in chat-run
businesses it's done entirely by hand: one person scrolls six groups, chases the silent ones, copies
the answers into one message and sends it on the weekend. Steward takes that over.

- **It saves the chaser's time.** Steward does the collecting, the reminding and the first draft.
  The founder only reads and replies "send".
- **The reminder goes only to the teams who are late.** The teams who already reported never get a
  "reminder to all".
- **Nobody needs to be a good writer.** A site supervisor can send a 1-minute voice note in Pidgin,
  Yoruba, Hausa, Spanish or Hindi, or a photo of the paper diary. Steward writes it up in plain English.
- **Promises get tracked.** Each check-in reminds the team of what it said it would do last week.
  The founder sees *promised vs done* per department, and slipping becomes visible early instead of
  at the end of the month.
- **Stuck work surfaces on its own.** The *stuck radar* flags a blocker that has appeared week after
  week, for example "cement order waiting on finance approval, stuck 3 weeks in a row". Those are
  the costly delays on a construction site.
- **Everyone sees the same picture.** The all-staff group gets one clear update every week, so
  departments know what the others are doing.
- **Private things stay private.** A lead can say "this is just for the MD". It goes into the
  founder's pack only, never to all staff.

---

## A week with Steward (construction example)

_The company and people below are made up. Every pack and voice brief shown is real output from
Steward's code, taken from the test data in `tests/`._

**Thursday 10:00, in the *Procurement* WhatsApp group**
> Hi Ngozi 👋 It's Steward, collecting this week's Procurement update for Adebayo Build Co.
> Reply with a voice note (any language is fine), a few lines, or a photo/file of your report…
> Last week you planned:
> • Get cement delivered to site
> • Book crane for level 3
> Quick word on how each went?

Ngozi replies with a 50-second voice note. Steward reads back three key points and asks one
follow-up question about the cement.

**In the *Site Operations* group:** Tunde sends a photo of the handwritten site diary. Steward reads it
with Hermes' vision tool and records the slab pour, the progress percentage and next week's blockwork.

**Friday 13:00:** Finance still hasn't replied, so only the Finance group gets a gentle reminder.

**Friday 15:00, the founder's private chat:** a status line, for example: ✅ Site Operations, ✅ Procurement,
✅ Safety, ⏳ Finance.

**Saturday 10:00, the founder's private chat:** the weekly pack, plus a voice note:
```
*Adebayo Build Co — weekly update*
_09 Nov – 13 Nov 2026_

*Reports in:* 3 of 4 (waiting on Finance)

*Last week*
*Site Operations*
Slab poured on level 2
• Poured slab on level 2
_Promised vs done: 1 of 1_
  ✅ Pour slab on level 2 — matched: “Poured slab on level 2”
📎 site-diary.jpg

*Procurement*
Cement finally ordered
• Crane booked for level 3
_Promised vs done: 1 of 2_
  ❌ Get cement delivered to site — approval still pending
  ✅ Book crane for level 3 — matched: “Crane booked for level 3”
🔒 For you only: Supplier hinted prices rise 8% next month

*Safety*
No incidents; 2 toolbox talks held

*Numbers*
• Site Operations — Overall progress: 42% (▲ 4)
• Procurement — Cement bags received: 180 (▼ 70)
• Safety — Incidents: 0 (new)

*Stuck radar*
🔴 Procurement: Cement order waiting finance approval — stuck 3 weeks in a row

*Next week (16 Nov – 20 Nov 2026)*
*Site Operations*: Start blockwork on level 2
*Procurement*: Receive 400 bags of cement
*Safety*: Scaffold inspection on level 3
```
And the voice brief (83 words, about 35 seconds):
> Here's your week at Adebayo Build Co. 3 of 4 teams reported; still waiting on Finance. Teams kept
> 2 of the 3 things they promised last week. The thing to unblock: Procurement has been stuck on
> cement order waiting finance approval for 3 weeks. Biggest move: Procurement, cement bags received,
> is down to 180. Site Operations: Slab poured on level 2. …
The founder replies **send**, and the staff version goes to *All Staff*.

---

## How it uses Hermes

Steward is built on Hermes' own features. It sends nothing by itself.

| Hermes feature | What Steward uses it for |
|---|---|
| **Cron scheduler** (`cronjob_manage`) with IANA timezones | every check-in, nudge, status and pack, at local company time |
| **Continuable deliveries** (`attach_to_session`) | department leads and the founder can *reply* to Steward's message and the agent keeps the context |
| **No-LLM script jobs** (`no_agent`) | nudges and the status line cost zero tokens; an empty output means nothing is sent, so only late teams get a reminder |
| **`failure_deliver`** | if a job fails, the error goes to the founder and never into a staff group |
| **Gateway voice transcription** | voice-note reports in any language |
| **`vision_analyze`** | photos of site diaries, whiteboards and printed sheets |
| **Gateway document handling** | PDF / Word / Excel reports, read with `steward_read_file` |
| **`text_to_speech`** | the founder's 60-second voice brief |
| **Plugin skills** (`steward:checkin`, `steward:weekly-pack`) | how to run the 2-minute interview and the approval flow |
| **`pre_llm_call` hook** | on chat platforms, each turn gets a short note naming the department groups, so a reply in a group is always recorded, even by a small model that has lost the check-in context |
| **Slash command** `/steward` | `status`, `pack`, `staff`, `send`, `checkin <dept>` |
| **Automation Blueprint** (v0.23) | "Weekly department reports" appears in the Hermes automations gallery |
| **Plugin storage** under `HERMES_HOME` | reports and copies of files sent in, with owner-only file permissions |

### Tools
| Tool | What it does |
|---|---|
| `steward_setup` | saves the company, timezone, departments and their chats, quiet hours and off days; (re)creates the schedule |
| `steward_checkin` | this week's check-in message, including last week's promises |
| `steward_read_file` | pulls the text out of PDF, Word, Excel, CSV or text reports |
| `steward_record_report` | saves a department's update: summary, done, blockers, next week, numbers, promise updates, private notes, attachments |
| `steward_status` | who has reported this week |
| `steward_build_pack` | founder or staff version of the weekly pack, plus the voice script |
| `steward_publish` | posts the staff pack to all staff, and **only** after the founder approves |

---

## Install

```bash
hermes plugins install imprint-hash/steward
```
Then, in any chat with your Hermes agent:
> Set up Steward for Adebayo Build Co, timezone Africa/Lagos. Departments: Site Operations (Tunde),
> Procurement (Ngozi), Safety (Musa), Finance (Bisi). Their WhatsApp groups are … The all-staff
> group is … Send my pack to this chat. Quiet hours 19:00 to 08:00, nothing on Sundays.

Hermes needs to be added to each department group, and group replies must be allowed in your
gateway settings. The default schedule is check-in on Thursday 10:00, nudge on Friday 13:00,
founder status on Friday 15:00 and the pack on Saturday 10:00. Every time can be changed.

**Running on a small or free model?** Steward works with Groq's free `openai/gpt-oss-120b`. We tested
it end to end: setup, a file report, a Pidgin voice-note report and the founder pack. Free plans cap
tokens per minute, so load only the tools Steward needs, e.g. `hermes chat -t steward`. On the gateway,
limit the platform's toolsets the same way. Groq's key also powers voice-note transcription
(`stt.provider: groq`).

Steward has no extra Python dependencies. PDF text extraction uses `pypdf` if it's installed;
otherwise Steward reads scanned PDFs with vision instead.

## Privacy
- Reports live in `HERMES_HOME/steward/state.json` (file mode 600) on the machine that runs Hermes.
  Copies of files that were sent in are kept next to it.
- `founder_only` notes, blockers and the late-teams list never appear in the staff version.
  Numbers stay founder-only unless `share_numbers_with_staff` is on.
- Nothing is posted to all staff without the founder's explicit "send" in the conversation.

## What Steward touches (disclosures)
- **Files:** settings and reports in `HERMES_HOME/steward/state.json` (mode 600). Copies of photos and files
  sent in as reports go in `HERMES_HOME/steward/files/<week>/<department>/`. `steward_read_file` and report
  attachments are limited to files people sent in the chat: Steward only reads or copies files inside
  Hermes's incoming media folders (`cache/images`, `cache/audio`, `cache/videos`, `cache/documents`),
  after resolving symlinks, and refuses any other path (such as `auth.json` or token files). The `week`
  value must look like `2026-W46`.
- **Scripts:** writes small helper scripts named `steward_*.py` into `HERMES_HOME/scripts/` for the no-LLM
  cron jobs (English check-ins, nudges, status, publish). They only load Steward's own `core.py` and
  never import Hermes internals. Re-running setup rewrites them, and they are safe to delete.
- **Cron jobs:** creates and removes its own jobs through Hermes's `cronjob_manage`, and only the jobs it
  recorded in its state.
- **Founder pack:** always an agent job (skill `steward:weekly-pack`) that calls Hermes's public
  `text_to_speech` tool for the voice brief, in every language. No generated script imports Hermes internals.
- **Not done:** no third-party network calls of its own, no shell commands, no stored credentials, no
  telemetry, no self-updating. Messages go out only through Hermes's gateway, and nothing is posted to
  all staff without the founder's approval in the conversation.

## Tests
```bash
python -m unittest discover -s tests -v
```
There are 114 tests, including security tests for file access, week paths, founder-only setup, publishing and reports, input limits, zip bombs and cron rules. They cover week maths across timezones and years, quiet hours, nudging only the
missing teams, promised vs done, the stuck radar, the numbers deltas, the founder vs staff packs,
Word, Excel and CSV parsing, the cron job plan and its replacement, the publish approval gate and
double-send protection. The plugin passes `hermes plugins validate`. We also ran it against Hermes
v0.23.1's real cron scheduler: Steward created its jobs and the no-LLM nudge jobs ran. The team
that hadn't reported got the reminder, and the team that had reported got nothing.

## License
MIT
