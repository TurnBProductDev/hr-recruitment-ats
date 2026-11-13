"""Who's hiring - shared context for the candidate-assessment prompts
(prompts/match_scoring.py and prompts/screening_questions.py), filled into
their {company_context} placeholder at call time. Lets the AI frame its
overview, concerns and questions around what this company actually sells
(e.g. "his SQL and Power BI skills help him understand TurnB's Analytics and
AI offerings"), not just the job title.

Edit here to change it everywhere. The admin-editable prompt rows keep the
{company_context} token, so an edit here still reaches them.
"""

COMPANY_CONTEXT = (
    "The hiring company is TurnB, an Analytics and AI consulting company. TurnB sells customised data "
    "analytics, business intelligence (e.g. Power BI dashboards, reporting, data engineering) and AI "
    "solutions to businesses - B2B technology services and consulting. Its client-facing roles therefore "
    "involve consultative selling and solution discussions with business decision-makers (CXOs, finance, "
    "operations and IT heads), not selling a standard retail product."
)
