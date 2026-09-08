"""Microsoft Graph client for Phase 2 of interview scheduling: a real Teams
meeting link (instead of HR pasting one into meeting_link by hand) and a
check against the interviewer's actual Outlook calendar (instead of only the
same-app check in Interview.conflicts_for()).

Inert until GRAPH_TENANT_ID/CLIENT_ID/CLIENT_SECRET/ORGANIZER_EMAIL are set -
is_configured() gates every call site, the same pattern as
candidates/match_scoring.py and candidates/cv_parser.py use for their own
Azure integrations. See graph_api/README.md for the Azure AD app
registration and Teams application access policy this depends on.

Every call here can raise GraphError. Callers must treat that as best-effort
- a Graph outage must never block scheduling an interview, only silently
skip the extra check/link this module would otherwise add.
"""
import logging
from datetime import timezone as dt_timezone

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

# One shared cache key: the token is app-wide (client-credentials, not
# per-user), so every request reuses it until shortly before it expires.
TOKEN_CACHE_KEY = 'graph_api:token'
TOKEN_SAFETY_MARGIN_SECONDS = 60


class GraphError(Exception):
    """Raised when a Graph call fails. Never let this propagate out of a
    scheduling action - catch it, log it, and fall back to the manual
    (pre-Phase-2) behaviour."""


def is_configured():
    return bool(
        getattr(settings, 'GRAPH_TENANT_ID', '') and getattr(settings, 'GRAPH_CLIENT_ID', '')
        and getattr(settings, 'GRAPH_CLIENT_SECRET', '') and getattr(settings, 'GRAPH_ORGANIZER_EMAIL', ''))


def _get_token():
    """App-only access token via the client-credentials flow, cached until
    shortly before it expires."""
    cached = cache.get(TOKEN_CACHE_KEY)
    if cached:
        return cached

    url = f'https://login.microsoftonline.com/{settings.GRAPH_TENANT_ID}/oauth2/v2.0/token'
    data = {
        'client_id': settings.GRAPH_CLIENT_ID,
        'client_secret': settings.GRAPH_CLIENT_SECRET,
        'scope': 'https://graph.microsoft.com/.default',
        'grant_type': 'client_credentials',
    }
    try:
        response = requests.post(url, data=data, timeout=15)
    except requests.RequestException as exc:
        raise GraphError(f'Could not reach Azure AD to get a Graph token: {exc}')

    if response.status_code >= 400:
        raise GraphError(f'Azure AD token request failed (HTTP {response.status_code}): {response.text[:300]}')

    try:
        payload = response.json()
        token = payload['access_token']
        expires_in = int(payload.get('expires_in', 0))
    except (ValueError, KeyError, TypeError):
        raise GraphError('Azure AD returned an unexpected token response shape.')

    cache.set(TOKEN_CACHE_KEY, token, max(60, expires_in - TOKEN_SAFETY_MARGIN_SECONDS))
    return token


def _request(method, path, **kwargs):
    token = _get_token()
    headers = kwargs.pop('headers', {})
    headers['Authorization'] = f'Bearer {token}'
    headers.setdefault('Content-Type', 'application/json')
    try:
        response = requests.request(
            method, f'{GRAPH_BASE}{path}', headers=headers, timeout=(10, 30), **kwargs)
    except requests.RequestException as exc:
        raise GraphError(f'Could not reach Microsoft Graph: {exc}')

    if response.status_code >= 400:
        raise GraphError(f'Microsoft Graph returned HTTP {response.status_code}: {response.text[:300]}')
    return response.json() if response.content else {}


def _iso_utc(dt):
    return timezone.localtime(dt, dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def is_interviewer_busy(interviewer_email, start, end):
    """True if the interviewer's real Outlook calendar shows them busy at any
    point between start and end (aware datetimes). Raises GraphError on
    failure - callers should treat that as "unknown, don't block".
    """
    body = {
        'schedules': [interviewer_email],
        'startTime': {'dateTime': _iso_utc(start), 'timeZone': 'UTC'},
        'endTime': {'dateTime': _iso_utc(end), 'timeZone': 'UTC'},
        'availabilityViewInterval': 15,
    }
    data = _request('POST', f'/users/{settings.GRAPH_ORGANIZER_EMAIL}/calendar/getSchedule', json=body)
    schedules = data.get('value') or []
    if not schedules:
        return False
    # One character per availabilityViewInterval-minute slot: '0' free,
    # '1'/'2'/'3' busy/tentative/out-of-office.
    view = schedules[0].get('availabilityView', '')
    return any(c != '0' for c in view)


def create_online_meeting(subject, start, end):
    """Create a Teams meeting organized by GRAPH_ORGANIZER_EMAIL and return
    its join URL. Raises GraphError on failure."""
    body = {
        'subject': subject,
        'startDateTime': _iso_utc(start),
        'endDateTime': _iso_utc(end),
    }
    data = _request('POST', f'/users/{settings.GRAPH_ORGANIZER_EMAIL}/onlineMeetings', json=body)
    join_url = data.get('joinWebUrl')
    if not join_url:
        raise GraphError('Microsoft Graph created the meeting but returned no joinWebUrl.')
    return join_url
