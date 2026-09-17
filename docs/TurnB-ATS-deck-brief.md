# TurnB ATS — Presentation Brief

**Purpose of this file:** everything needed to build a PowerPoint deck about the TurnB ATS
applicant tracking system — the context, the approved content, the slide flow, and the
rules about what may and may not be claimed. Hand this whole file to Claude (or any deck
tool) as the source.

**Every number in this file was queried from the live production database on 15 September
2026.** Nothing here is invented. Don't paraphrase from memory and don't round figures up.

Companion file: `TurnB-ATS-value-brief.html` — the same material as a formatted web document.

---

## 1. Context

| | |
|---|---|
| **Product** | TurnB ATS — applicant tracking system for TurnB Business Services Pvt. Ltd., Edapally, Kochi |
| **Built with** | Django + Bootstrap 5, Azure App Service, Azure SQL, Azure OpenAI, Azure Logic Apps, Microsoft Graph, Microsoft Entra ID |
| **Deck audience** | HR leadership and company management — non-technical. They care about time, cost, control and risk, not architecture. |
| **Deck purpose** | Show what the desk used to do by hand, what the system does instead, what 81 days of live running proves, and what still needs switching on |
| **Measurement window** | 25 June – 14 September 2026 (81 days = 2.66 months). July and August are the only complete months. |
| **Tone** | Confident and concrete. No hype. Every claim is backed by a count or is demonstrable in the app. |
| **Length** | 17 slides full version; a 10-minute cut is marked below |

### Non-negotiable honesty rules

1. **No hire has been recorded in the system.** One candidate reached Final Selection;
   zero reached Hired. The deck must make **no claim** about time to hire, cost per hire,
   or quality of hire. If someone asks, the honest answer is "the pipeline hasn't closed
   one inside the system yet."
2. **Volumes are measured; per-task manual times are estimated.** The ~76 hours saved is
   real volumes × estimated manual minutes. Say this once, on the effort-model slide.
3. **Five features are built but barely used.** They get their own slide (14). Do not
   present them elsewhere as though they are in daily use — in particular, do not claim
   Teams links are auto-generated or that interviewers propose their own slots. They can't
   and don't, yet.
4. **Don't claim the AI decides anything.** It scores, drafts and extracts. A named person
   is recorded against all 924 logged actions.
5. **Cite the window.** "In the first 81 days" or "since late June" — never an unqualified
   "we handle 188 applications a month" as though it were a steady-state figure.

---

## 2. The measured data

Reference table. Use these exact figures; don't re-derive.

### Volume

| Metric | Value |
|---|---:|
| Candidates handled | **499** |
| Window | 25 Jun – 14 Sep 2026 (81 days) |
| By month | Jun 10 · Jul 167 · Aug 256 · Sep 66 (to the 14th) |
| Monthly rate | **188 / month** (499 ÷ 2.66) |
| Vacancies created | 7 (5 open, 2 closed; 6 openings) |
| User accounts | 17 — 1 HR Admin, 1 Recruiter, 14 Interviewers, 1 Hiring Manager |

### Where candidates came from

| Source | Count |
|---|---:|
| LinkedIn | 297 |
| Careers portal | 193 |
| Agency | 3 |
| Referral | 3 |
| Naukri | 2 |
| Other | 1 |

### Where they are now

| Status | Count |
|---|---:|
| Open | 193 |
| Rejected | 160 |
| On hold | 90 |
| Qualified | 37 |
| Round 1 | 14 |
| Round 2 | 5 |
| **Hired** | **0** |

### Funnel — candidates who ever reached each stage

| Stage | Count |
|---|---:|
| Qualified | 116 |
| Round 1 | 39 |
| Round 2 | 11 |
| Final Selection | 1 |
| Hired | 0 |

### What the system did automatically

| Metric | Value |
|---|---:|
| CVs with an AI-written profile | **431 of 499 (86%)** |
| Candidates with CV on file | 499 (100%) |
| Skills extracted | 350 |
| Education captured | 486 |
| Employment history captured | 30 |
| Candidates scored against a JD | **274** |
| Score spread | <40: 82 · 40–59: 117 · 60–74: 55 · 75+: 20 |
| Repeat applicants flagged | **173** |
| Addresses that applied more than once | 191 |
| Most applications from one address | **12** |
| Audit entries recorded | **924** (499 creations + 425 decisions) |
| Notes on timelines | 148 |
| Calls logged | 79 (73 phone, 6 interview) |

### Speed

| Metric | Median | Mean | Max |
|---|---:|---:|---:|
| Application → Qualified | **4 days** | 9.7 | 47 |
| Application → Rejected | **13 days** | 14.3 | 52 |

### Interviews

| Metric | Value |
|---|---:|
| Interviews scheduled | 22 |
| Distinct interviewers | 6 |
| Reschedules handled | 8 |
| Status | 11 completed · 8 cancelled · 2 rescheduled · 1 scheduled |
| Result | 3 pass · 8 fail · 11 pending |
| Mode | all 22 video |

### The adoption gap — built but barely used

| Capability | Actual use |
|---|---:|
| Interviewer proposes own slots | **0 times** |
| Auto Teams link on invite | **0 of 22 interviews** |
| AI tele-screening questions | **4 of 499 candidates** |
| Bulk Upload CV | 12 candidates (16 of 17 files parsed OK) |
| Structured employment history | 30 of 499 |

---

## 3. Narrative flow

```
ACT 1  The problem      →  "Recruitment ran on a mailbox and a spreadsheet, and every
   (slides 3–4)            step meant retyping something that already existed."

ACT 2  The replacement  →  "Here is what happens instead at each stage — with what the
   (slides 5–11)           database shows for the first 81 days."

ACT 3  The payoff       →  "That removes about 76 hours of manual work a month."
   (slides 12–13)

ACT 4  The honest part  →  "Five things are built and not being used. Here's the ask."
   (slides 14–16)
```

The fourth act is what makes this deck credible rather than promotional. It also converts
the presentation into a decision meeting: four of the five gaps close with a management
decision, not a development budget.

---

## 4. Slide-by-slide content

**SN** = speaker notes. **[CUT-10]** = drop for a 10-minute version.

---

### Slide 1 — Title

**Headline:** From Inbox to Hire
**Subtitle:** TurnB ATS — the first 81 days
**Footer:** 25 June – 14 September 2026

---

### Slide 2 — Executive summary

Four figures, equal weight:

| Figure | Caption |
|---|---|
| **499** | candidates handled in the first 81 days — none typed in by hand |
| **86%** | of CVs read and filled in by the system: 431 of 499 |
| **173** | repeat applications caught automatically — one address applied 12 times |
| **~76h** | of manual admin removed per month, on measured volumes |

**SN:** Say up front that the first three are counts from the database and the fourth is a
model built on those counts. Then promise the adoption-gap slide — "I'll also show you five
things we built that nobody's using."

---

### Slide 3 — The old working day (part 1)

1. **Opening the inbox and typing it all in again** — every CV opened, read, and its name,
   phone, email, qualification and experience keyed into the spreadsheet by hand.
2. **No way to know who had applied before** — a repeat applicant looked identical to a
   first-time applicant.
3. **Screening by reading, with nothing written down** — the reasoning behind a shortlist
   lived in the recruiter's head, so it couldn't be reviewed or defended later.
4. **A spreadsheet with no memory** — status was a cell that got overwritten. Two people
   editing at once meant one lost their work.

---

### Slide 4 — The old working day (part 2)

5. **Email ping-pong to book one interview** — ask the interviewer, wait, offer times to the
   candidate, wait, make the invite, paste a link, repeat when one side moved.
6. **Interviewers with no window into the process** — CVs forwarded as attachments, feedback
   returned as prose, HR transcribing the verdict.
7. **Writing the same emails over and over** — wording drifting further from the approved
   version each time.
8. **Month-end reporting rebuilt by hand** — a day's work, out of date on arrival.

**SN:** "Every one of those eight is a person moving information that already existed from
one place to another."

---

### Slides 5–10 — The pipeline, stage by stage

One slide per stage, identical layout: stage number and name, system statuses as a small
caption, Before / Now split, then **a measured-evidence line at the bottom in the ochre
accent**. That last line is new and it's what makes this deck different from a feature tour.

---

#### Slide 5 — Stage 1: Applications arrive

**Before:** Open each mail, read the attachment, type the applicant in, save the CV to a
folder, hope the file name matches the row.

**Now:** A CV mailed to the careers address is read by the system and written straight into
the candidate database — name, contact, education, skills and a written profile summary —
with the file archived and linked on the record. Nobody opens the mailbox to make it happen.

**Evidence:** 499 candidates in 81 days — 167 in July, 256 in August. 100% have their CV on
file; 86% arrived with an AI-written profile already attached. LinkedIn supplied 297, the
careers portal 193.

---

#### Slide 6 — Stage 2: De-duplication and filing

**Before:** Duplicate and repeat applicants went unnoticed unless someone recognised the
name. Speculative CVs with no matching vacancy piled up unsorted.

**Now:** Every applicant email is checked against the registry as the record is created — a
reapply is flagged automatically, a blacklisted address is blocked. A CV matching no open
vacancy is filed under General Applications against the role the candidate asked for.

**Evidence:** 173 candidates flagged as repeat applicants. 191 addresses have applied more
than once; the busiest applied 12 times. 96 speculative CVs are filed and searchable.

**SN:** This is the most quietly valuable slide. Before the system, all 173 of those were
invisible.

---

#### Slide 7 — Stage 3: CV screening

**Before:** Read every CV against the JD, form a view, move a cell. No score, no written
reason, no consistency between two recruiters or between Monday and Friday.

**Now:** Score Candidates runs a whole vacancy's applicants against its job description and
returns one score per candidate with a written rationale — judging whether the person could
do the job, not how many keywords their CV contains. HR reads the score and the reasoning,
then decides. Criteria and the AI's instructions are both admin-editable.

**Evidence:** 274 candidates scored. The spread is doing real work — 82 scored under 40,
only 20 above 75. Median application → qualified decision: 4 days.

**SN:** The score spread is the proof it isn't rubber-stamping. If everything scored 70–80
the feature would be worthless.

---

#### Slide 8 — Stage 4: Tele screening **[CUT-10]**

**Before:** Each recruiter invented their own call questions, and the outcome was a note in
a cell — if recorded at all.

**Now:** Every call and outcome is logged on the timeline and feeds the dashboard's
call-stage counts. Ten questions can be generated from the candidate's CV and saved to the
record for whoever interviews them later.

**Evidence:** 79 calls logged, plus 148 notes on timelines — a written history where
previously there was none. Generated question sets used on only 4 candidates so far.

---

#### Slide 9 — Stage 5: Interview rounds

⚠️ **Wording here is deliberately narrower than the feature list. Do not widen it.**

**Before:** Emails back and forth to find a slot, a calendar invite made by hand, a CV
forwarded as an attachment, feedback typed up from a reply.

**Now:** Interviews are scheduled against the candidate's record and the invitation goes out
as a real calendar meeting the candidate can accept. Reschedules and cancellations are
tracked rather than lost in a mail thread, and the interviewer enters feedback, score and
pass/fail directly in their own portal — no transcription by HR.

**Evidence:** 22 interviews across 6 interviewers, 8 reschedules handled on the record. 14
of 17 accounts are interviewers. Results: 3 pass, 8 fail, 11 awaiting a verdict.

**SN:** Don't claim the slot-proposal flow or auto Teams links here — both are on slide 14
as gaps. Mention that 11 pending verdicts is itself a finding: the system surfaces the
follow-up that used to be invisible.

---

#### Slide 10 — Stage 6: Decision and close-out

**Before:** Rejection mails retyped one at a time, wording drifting with each copy, and
candidates under a filled vacancy closed off individually or forgotten.

**Now:** The rejection mail arrives pre-written in approved wording, addressed and copied to
the right people, for HR to review and send. Candidates can be closed in bulk, one action
rejects everyone still open under a filled vacancy, and a mis-click can be reverted.

**Evidence:** 160 rejections issued in consistent, approved wording. Median application →
rejection: 13 days — candidates get an answer instead of silence. One candidate has reached
Final Selection; no hire recorded yet.

---

### Slide 11 — Running across every stage

2×2 grid, no numbering:

- **A permanent audit trail — 924 entries.** Every status change stored with who, when and
  why, driving the Stage Dates tracker. 499 are record creations; 425 are decisions someone
  made and the system remembered. Nothing overwritten.
- **Reporting already built.** Applicants → Qualified → Shortlisted → R1 → R2 → Hired with
  conversion at each step, by role and source. Today: 116 ever qualified, 39 reached Round 1,
  11 reached Round 2. Every bar drills through to the records behind it.
- **Access by role, not by forwarding.** 17 accounts across four roles, signing in with their
  existing Microsoft work account. The 14 interviewers get their own portal.
- **Exports where still needed.** Any filtered list downloads to Excel in one click — the
  spreadsheet is an output now, not the system of record.

---

### Slide 12 — The measured volumes

**This slide must come before slide 13. Do not merge them.**

> **The volumes are measured, not assumed** — counted from the system over the 81 days from
> 25 June to 14 September 2026 and converted to a monthly rate. The per-task manual times
> they're multiplied by are still estimates. Those are the numbers to challenge.

| Measured over 81 days | Total | Monthly rate |
|---|---:|---:|
| Applications received | 499 | 188 |
| Screening & stage decisions | 425 | 160 |
| Rejections sent | 160 | 60 |
| Calls logged | 79 | 30 |
| Interviews scheduled | 22 | 8 |
| Vacancies opened | 7 | 3 |

---

### Slide 13 — Where the time goes

Paired horizontal bar chart, before on top (clay) and now beneath (teal). **Use these exact
values. Scale the axis to 2,000.**

| Activity | Before (min/mo) | Now (min/mo) | Saved | % cut |
|---|---:|---:|---:|---:|
| CV screening against the JD | 1,880 | 376 | 1,504 | 80% |
| Logging applicants into the repository | 1,504 | 15 | 1,489 | 99% |
| Duplicate and repeat-applicant checks | 376 | 0 | 376 | 100% |
| Rejection emails | 360 | 90 | 270 | 75% |
| Tele-screening preparation | 300 | 15 | 285 | 95% |
| Status tracking and chasing updates | 240 | 20 | 220 | 92% |
| Monthly reporting pack | 240 | 5 | 235 | 98% |
| Interview scheduling and invitations | 200 | 40 | 160 | 80% |
| Vacancy set-up from a JD | 45 | 9 | 36 | 80% |
| **Total** | **5,145** | **570** | **4,575** | **89%** |

Then the totals, three across:

| | |
|---|---|
| **86 hours** | manual admin per month, before |
| **9.5 hours** | manual admin per month, now |
| **76 hours** | given back — 89%, about 9½ working days a month |

Closing line:

> Two activities account for two-thirds of the saving. Logging applications and checking for
> repeat applicants was pure transcription — at 188 applications a month it consumed over
> thirty hours, and it's now essentially gone. Screening hasn't disappeared but has changed
> shape: from reading 188 CVs cold to reviewing a score and a rationale, then deciding.

---

### Slide 14 — Built, not yet switched on

**Message:** Five capabilities are finished and working but barely used. Four close with a
decision, not a budget.

| Capability | Used | Why it matters |
|---|---:|---|
| Interviewer proposes own slots | **0 times** | The availability email loop is still run by hand — the biggest remaining manual cost per interview |
| Auto Teams link on the invite | **0 of 22** | Needs Graph credentials set in the live environment; links are pasted by hand until then |
| AI tele-screening questions | **4 of 499** | Built and working — recruiters simply aren't opening it |
| Bulk Upload CV | **12 CVs** | Agency batches still arrive one at a time; 16 of 17 attempted files parsed fine |
| Structured employment history | **30 of 499** | Education captured for 486 but employment for only 30 — this one needs an extraction fix, not a decision |

**SN:** This is the ask. Frame it as "the system is already returning 76 hours a month
without these five — here's what's still on the table." Row 5 is the only one that needs
developer time.

---

### Slide 15 — What we deliberately left to people

- **No candidate is rejected by a score.** The score and rationale are an input. Status only
  changes when someone changes it.
- **No email sends itself to a candidate.** Invitations and rejections are drafts; the
  recruiter reads, edits and sends. Recipients and CC fixed by policy.
- **Interview verdicts are the interviewer's.** Feedback, score and pass/fail entered by the
  person who ran the round.
- **Offers, negotiation and the hire decision stay off-system.** The app records the outcome;
  it doesn't make it.
- **The wording is yours to change.** Email templates and the AI's instructions are both
  admin-editable, so tone and criteria stay under HR's control.

**SN:** A named person is recorded against all 924 actions — that's the evidence this isn't
rhetoric.

---

### Slide 16 — How to read these numbers **[CUT-10]**

Put the methodology on the record rather than in a footnote:

- Every count queried from the live database on 15 September 2026.
- Window: 25 June – 14 September 2026 (81 days). July and August are the only complete months.
- Monthly rates = period total ÷ 2.66 months.
- Effort model: measured volumes × estimated per-task manual times.
- **No hire has been recorded in the system**, so this deck makes no claim about time to
  hire, cost per hire or quality of hire.

---

### Slide 17 — Built on

Django and Bootstrap 5 on Azure App Service against Azure SQL. CV and JD reading, match
scoring and screening questions on Azure OpenAI. Mail and document filing through Azure
Logic Apps. Interview calendars through Microsoft Graph. Staff sign-in through Microsoft
Entra ID.

---

## 5. Design direction

| Element | Value |
|---|---|
| Background | `#EDF0F0` light (`#0F1513` if a dark deck is wanted) |
| Body text | `#15201F` |
| Accent — "now", automated, positive | teal `#0B6B60` |
| Accent — "before", friction, manual | muted clay `#9C5433` |
| Measured evidence and headline figures | ochre `#8C6F1B` |
| Heading font | Archivo (PowerPoint-safe fallback: Segoe UI Semibold) |
| Body font | Source Serif 4 (fallback: Georgia) |
| Numbers, labels, status codes | IBM Plex Mono (fallback: Consolas) |

Rules that carry the whole deck:

- **Teal always means "now", clay always means "before", ochre always means "this is a
  measured number".** Never swap them. Once the audience learns the code on slide 5 they read
  every later slide faster.
- Keep the Before / Now split identical on all six stage slides.
- Status codes (`OPEN`, `QUALIFIED`, `ROUND 1`) in the mono face, small and uppercase.
- Numbers right-aligned with tabular figures everywhere.
- No emoji section markers, no stock photos of people shaking hands.

---

## 6. Prompt to paste with this file

> Build a PowerPoint deck from the brief below. Follow its slide flow and use its exact
> wording and figures — every number was queried from the live database and must not be
> changed, rounded or re-derived. Honour the honesty rules in section 1: no claims about
> time to hire (no hire has been recorded), the measured-volumes slide must precede the
> savings slide, and slide 9's narrower wording must not be widened to include features
> listed as gaps on slide 14. Use the design direction in section 5 with PowerPoint-safe
> font fallbacks. 17 slides.
