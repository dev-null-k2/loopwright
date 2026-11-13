---
name: schedule-meeting
description: Book a meeting, call or catch-up on the user's calendar, respecting stored preferences.
triggers: schedule, book, set up a, arrange, meeting, catch-up, catch up, call with, sync with, 1:1
---
When the user asks you to book time with someone:

1. Work out who, which day and what time. A weekday name means the next one after today.
2. If no time is given, look for a stored preference about that person or about the user
   ("prefers mornings", "never before 10"). Check the Memory section first; if it is not
   there, call `recall` with the person's name.
3. If there is still no time, choose 10:00 and say that you chose it.
4. Call `create_event` once, with the date as YYYY-MM-DD and the time as HH:MM.
5. Confirm in one sentence: the title, the day and date, the time, and which stored
   preference you used. If the tool reports an overlap, say so.

Never invent an attendee's email address. Never send an invitation; booking is local.
