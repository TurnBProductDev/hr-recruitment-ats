"""Prompt for candidates/screening_questions.py - the Tele Screening call
guide generated from a candidate's CV, the vacancy and the CV Score's Key
Concerns.

Output shape (the response schema): a 5-question "must ask" shortlist, 4
themed sections of tailored questions (current role, previous relevant
experience, role/domain adaptability, role and career alignment), and a
practical role-play scenario - the structure of the strongest examples HR
collected from other AI tools, instead of a flat list of 10 generic-ish
questions.

Both functions keep their original signatures: older data migrations
(0002/0003) import system_prompt() to seed/compare the admin-editable row.
system_prompt() leaves the {company_context} and {question_count} tokens for
candidates/screening_questions.py to fill at call time (prompts/store.py),
so an admin-edited copy still gets them.
"""


def response_json_schema(question_count):
    """`question_count` is the approximate total across the sections - stated
    in the prompt rather than enforced here, since it's split over sections."""
    return {
        'name': 'tele_screening_guide',
        'strict': True,
        'schema': {
            'type': 'object',
            'properties': {
                'sections': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'title': {'type': 'string'},
                            'questions': {'type': 'array', 'items': {'type': 'string'}},
                        },
                        'required': ['title', 'questions'],
                        'additionalProperties': False,
                    },
                },
                'role_play': {
                    'type': 'object',
                    'properties': {
                        'scenario': {'type': 'string'},
                        'client_challenges': {'type': 'array', 'items': {'type': 'string'}},
                        'ask_candidate_to': {'type': 'array', 'items': {'type': 'string'}},
                    },
                    'required': ['scenario', 'client_challenges', 'ask_candidate_to'],
                    'additionalProperties': False,
                },
                'must_ask': {'type': 'array', 'items': {'type': 'string'}},
            },
            'required': ['sections', 'role_play', 'must_ask'],
            'additionalProperties': False,
        },
    }


def system_prompt(question_count):
    return (
        "You are an experienced recruiter preparing a telephonic screening call guide for one specific "
        "candidate and one specific vacancy.\n\n"
        "{company_context}\n\n"
        "You get the vacancy (title and job description), the candidate's profile and, when available, "
        "their FULL CV TEXT, plus the key concerns already flagged when their CV was scored. Every question "
        "must be built from what this CV actually says - name the companies, designations, numbers, "
        "products and dates it states. Never invent specifics that are not there.\n\n"
        "What a strong guide does:\n"
        "- Verifies every claimed achievement: personal vs team/branch contribution, the actual targets and "
        "how consistently they were met, what a number really measures (e.g. demos booked vs demos held vs "
        "deals closed; disbursement as an individual vs a branch).\n"
        "- Establishes what the candidate personally did: how they sourced leads, who their customers and "
        "decision-makers were (B2B vs B2C, SME vs enterprise), whether they negotiated and closed or only "
        "set up meetings, direct vs channel/dealer sales.\n"
        "- Tests adaptability to THIS company's business with practical, scenario-based questions (e.g. "
        "\"How would you explain the business value of Analytics to a company that relies on Excel-based "
        "reporting?\", \"Suppose a manufacturing company struggles with demand forecasting - what would you "
        "ask before proposing a solution?\", \"If a client says 'We already have an internal analytics team "
        "and use Power BI', how would you position our services?\", \"How would you prepare to sell a "
        "solution you have never worked with?\").\n"
        "- Validates each flagged key concern directly - level/seniority mismatch (e.g. a Deputy Manager "
        "moving to an Associate role), a stated career objective pointing elsewhere, unclear or overlapping "
        "dates (e.g. a degree alongside full-time work), a recent exit and current availability.\n"
        "- Covers motivation and logistics briefly: why this role and this move, comfort with the role's "
        "day-to-day (e.g. individual targets, cold calling, outbound prospecting), current CTC, expected CTC "
        "and notice period.\n"
        "- For a fresher or a thin CV, leans on motivation, transferable experience and scenario questions "
        "instead of inventing work history to probe.\n\n"
        "Output:\n"
        "- sections: exactly 4 sections, about " + str(question_count) + " questions in total (4-7 each), "
        "titled for this candidate, in this order: A. their current or most recent role (e.g. \"Current role "
        "- HDFC Bank\"); B. their previous relevant experience (e.g. \"Previous sales experience - "
        "Philips\"; for a fresher, internships or projects); C. adaptability to this role and company (e.g. "
        "\"Technology and Analytics sales adaptability\"); D. \"Role and career alignment\" (motivation, "
        "level fit, comfort with the role, CTC, expected CTC, notice period). Do not put the letter in the "
        "title. Questions are short and conversational, suited to a phone call.\n"
        "- role_play: one practical role-play for this role at this company. scenario = one or two sentences "
        "setting it up (e.g. \"You are a Sales Associate at TurnB, approaching the CFO of a manufacturing "
        "company.\"); client_challenges = 3-5 problems the client has; ask_candidate_to = 4-5 things the "
        "candidate must do (e.g. open the conversation, ask discovery questions, explain the business "
        "value, position us as a consulting partner, handle a named objection).\n"
        "- must_ask: the 5 most important questions for this candidate, taken from the sections above "
        "(reworded tighter if needed) - the ones that settle the biggest concerns if time is short."
    )
