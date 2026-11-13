"""The TurnB ATS user guide "Ask the ATS" answers "How do I..." and "What
is..." questions from. Kept as plain text in the system prompt (it's small),
so update it here when a feature changes - the assistant only knows what is
written here about how the app works.
"""

HELP_TEXT = """\
TURNB ATS - USER GUIDE

NAVIGATION (top bar)
- Home: the HR Dashboard, with three tabs - Summary, Overview, Daily View - and filters for Job Code, Vacancy
  and "Open vacancies only".
- Candidates > Repository: every candidate, with search and filters (status, vacancy, score). Click a name to
  open their profile.
- Candidates > General Applications: applicants not mapped to a specific vacancy ("General Application").
- Candidates > Future Prospects: candidates put on Hold before they were ever screened - a pool to revisit for
  future openings. They are left out of the dashboard numbers.
- Vacancies: create and manage jobs (title, job code, location, description, requirements, must-have
  requirements, number of openings, JD file, open/closed, archive). The careers site lists open vacancies.
- Interviews: interviews awaiting a result (upcoming and overdue), with Update Status, Reschedule and Review &
  Send Invite actions. It can also be opened filtered (Today, Upcoming, Result Pending, Overdue) from the
  dashboard's Interview tiles.
- Actions > Score Candidates: run AI scoring for a vacancy's unscored candidates.
- Actions > Bulk Upload: upload many CVs at once; the AI reads each CV and creates the candidates.
- Actions > Scoring Criteria: extra per-role instructions for the AI scorer, plus "Rescore This Role" to
  re-score everyone in that role after changing them.
- Reports: recruitment reports.
- Bell icon: notifications. Account menu: View Careers Site, Users (manage accounts and roles), Change Password,
  Logout.

CANDIDATE PIPELINE (statuses, in order)
- Open (shown as "Applied" / CV Screening): new application, not yet screened.
- Qualified (status SHORTLISTED): passed CV screening; now in Tele Screening (phone call stage).
- Round 1: moved on after the call; first interview round.
- Round 2 (status INTERVIEW): cleared Round 1; final interview round.
- Final Selection: cleared Round 2; awaiting the hire decision.
- Hired.
- Rejected / Blacklisted: out of the process (Blacklisted also blocks future applications).
- Hold: paused at whatever stage they were at; resuming continues from the next stage. A Hold taken before
  screening makes them a Future Prospect.

CANDIDATE PROFILE PAGE (open a candidate from the Repository)
- Candidate Details: contact info, qualification, experience, CV link.
- AI-Scan: the AI CV Summary (with the role-specific Candidate Overview at the top once scored) and the CV
  Score card (score, band, factor ratings, Key Strengths, Key Concerns to Validate, rationale, Re-score button).
- Hiring: a stage-by-stage card (CV Screening, Tele Screening, Round 1, Round 2, Final Decision) with that
  stage's actions - Qualify / Reject / Hold at CV Screening; log the call, View/Generate Questions,
  Shortlist / Reject / Hold at Tele Screening; schedule or allocate an interview, mark it done and record the
  result at Round 1/Round 2; Hire / Reject at the Final Decision. Every status change needs remarks. The most
  recent action can be undone.
- History: the full timeline of status changes, calls, notes and interviews.

HOW DO I...
- Add a candidate: Actions > Bulk Upload (CV files), or they arrive automatically from the careers site /
  careers mailbox intake.
- Score candidates: Actions > Score Candidates and pick the vacancy; or open a candidate and click Re-score on
  the CV Score card. Re-score uses the latest scoring rules.
- Change how a role is scored: Actions > Scoring Criteria (extra instructions per role), or set the vacancy's
  Must-have requirements under Vacancies; then "Rescore This Role".
- Move a candidate forward or reject them: open their profile and use the buttons on the current stage card in
  the Hiring section (remarks are required).
- Get tele-screening questions: on the Tele Screening stage card click Generate/View Questions. The guide has a
  "Must ask" shortlist, four question sections and a practical role-play; click Regenerate for a fresh one
  (re-score first so it uses the latest Key Concerns).
- Schedule an interview: on the Round 1 / Round 2 stage card, schedule it directly (date, interviewer, mode -
  video interviews get a Teams meeting) or allocate an interviewer, who proposes slots that the candidate then
  picks and HR approves. Reschedule / Cancel / Mark Done from the stage card or the Interviews page.
- Record an interview result: Interviews page > Update Status, or the stage card. Pass moves the candidate to
  the next stage; Fail rejects them.
- Put someone on Hold / resume them: Hold on the stage card; resume from the same card later.
- Find overdue interviews: Home > Summary > Interviews section > Overdue tile.
- See today's activity: Home > Daily View (pick a date range).
- Add a user or change roles: account menu > Users.

WHAT IS...
- CV Score: the AI's 0-100 read of how well the candidate fits their vacancy, from the full CV (when readable),
  the job description and must-haves. Bands: 85-100 Strong Fit; 70-84 Strong Potential - Minor Gaps; 55-69
  Potential - Significant Gaps; 40-54 Consider (no real hands-on experience of the role's core work); below 40
  Unfit. Factor ratings (0-10): Core experience, Domain, Seniority, Results, Education & skills.
- Key Strengths / Key Concerns to Validate: evidence for the fit, and the specific points HR should confirm on
  the call. Older scores show Likes / Not Matched instead until re-scored.
- Candidate Overview: the AI's role-specific summary of the candidate, written at scoring.
- Dashboard Summary tab: Open Positions (sum of openings on open vacancies), Total Applicants, Screened
  (candidates HR has acted on), Active Pool (Qualified through Final Selection, plus Holds after screening),
  Rejected, Hired; tables By Job / Role and By Source; Interviews section with tiles Today, Upcoming (all
  future interviews), Result Pending (marked done, no result yet), Overdue (time passed, not marked done) and
  a By Role / Vacancy table.
- Dashboard Overview tab: the hiring funnel. Each stage's bar shows its green "cleared" value and its breakdown
  (Rejected, Hold, Yet to Call, Scheduled, Decision Pending...). Each row's right-hand total is the previous
  stage's green value. Round 1 / Round 2 expand into Interview Done (Cleared, Rejected, Hold, Decision Pending)
  plus Scheduled / Yet to Schedule / Not Turned Up.
- Dashboard Daily View: how many actions HR took per stage in a date range; the bar shows distinct candidates,
  the tooltip header the total actions, and its rows count each candidate once under their latest action.
- Screening Pending: candidates still Open (no screening decision yet).
- Decision Pending: an interview marked Done with no result recorded.
- Future Prospect: held before screening - kept for future openings, not counted as an active application.
- General Application: an applicant who didn't apply for a specific open vacancy.
"""
