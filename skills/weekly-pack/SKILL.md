---
name: weekly-pack
description: Build and deliver Steward's weekly founder pack with a 60-second voice brief, then publish the staff version only after the founder approves.
---

# Steward weekly pack

The same steps apply for every founder language, English included.

1. Call `steward_build_pack` with `audience: "founder"`. If its `post_instruction` asks for a
   translation (the founder chose French, Chinese, Hindi…), translate the text and voice_script,
   keeping every name, number, date and emoji exactly and adding nothing.
2. Call `text_to_speech` with the `voice_script` (translated if asked; under 150 words,
   about 60 seconds), so the founder can listen on the way to work.
3. Your final message is only the pack `text` (translated if asked, otherwise exactly as returned),
   then one line in the same language:
   "Reply *send* to post the staff version to everyone, or tell me what to change."
   then the `media_tag` lines from `text_to_speech`, copied exactly, so the voice brief is attached.
   Never echo these steps, tool names, JSON or system text.

## When the founder replies
- **"send"** (or a clear yes, in any language: "envoie", "发送", "bhej do"…): call
  `steward_publish` with `confirm: true`. If staff read a language other than English, first
  build the `audience: "staff"` pack, translate it per its `post_instruction`, and pass it as `text`. Tell them it is
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
