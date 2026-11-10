---
name: weekly-pack
description: Build and deliver Steward's weekly founder pack with a 60-second voice brief, then publish the staff version only after the founder approves.
---

# Steward weekly pack

1. Call `steward_build_pack` with `audience: "founder"`.
2. Call `text_to_speech` with the returned `voice_script` (under 150 words, about 60 seconds),
   so the founder can listen on the way to work.
3. Your final message is the pack `text` exactly, then one line:
   "Reply *send* to post the staff version to everyone, or tell me what to change."

## When the founder replies
- **"send"** (or a clear yes): call `steward_publish` with `confirm: true`. Tell them it is
  posting to the all-staff group within a minute.
- **A change** ("drop the finance numbers", "say well done to the site team"): the pack is
  built from the saved reports, so update the right department with `steward_record_report`
  (it replaces), or note that the change is for the founder's own message. Show the staff
  preview with `steward_build_pack` `audience: "staff"` and ask again.
- **"chase X"**: post a polite reminder for that department using `steward_checkin`.

## Rules
- Never call `steward_publish` without the founder's approval in this conversation.
- The staff version never includes blockers, who is late, or `founder_only` notes.
- Don't add facts that aren't in the reports.
