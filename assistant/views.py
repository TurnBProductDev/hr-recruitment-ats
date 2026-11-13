import json

from django.http import JsonResponse
from django.views import View

from candidates.permissions import ANY_STAFF, GroupRequiredMixin

from . import engine


class AskView(GroupRequiredMixin, View):
    """POST {messages: [{role, content}...], candidate_id?} -> {reply}.
    HR Admin / Recruiter / Hiring Manager only (ANY_STAFF) - not the
    Interviewer portal. Read-only: see assistant/tools.py."""
    allowed_groups = ANY_STAFF

    def post(self, request):
        try:
            body = json.loads(request.body or b'{}')
        except ValueError:
            return JsonResponse({'error': 'Bad request.'}, status=400)
        try:
            history = engine.clean_history(body.get('messages'))
            candidate_id = body.get('candidate_id')
            candidate_id = int(candidate_id) if str(candidate_id or '').isdigit() else None
            reply = engine.answer(history, candidate_id=candidate_id, user=request.user)
        except engine.AssistantError as exc:
            return JsonResponse({'reply': str(exc), 'error': True})
        return JsonResponse({'reply': reply})
