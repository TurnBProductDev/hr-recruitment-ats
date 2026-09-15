"""Sign in with Microsoft: lets someone who is already a Django User here
(with a role/Group already assigned on the Users page) skip typing a
password by verifying their identity with the org's own Azure AD /
Microsoft Entra ID tenant instead - where they must also already be added as
an external (B2B guest) user. Identity only: this module never decides what
a signed-in account can do, that stays entirely in Django's own Groups
(candidates/permissions.py), exactly as for a password sign-in.

Inert until AZURE_AD_TENANT_ID/CLIENT_ID/CLIENT_SECRET are set -
is_configured() gates the "Sign in with Microsoft" button and both views in
auth_views.py, the same pattern candidates/logic_app_mail.py and
interviews/graph_client.py use for their own Azure integrations.

Implemented as direct calls to the v2.0 authorize/token endpoints plus one
Microsoft Graph /me call (matching graph_client.py's own requests-based
style) rather than pulling in the MSAL library for what is otherwise a
plain OAuth2 Authorization Code exchange.
"""
import logging
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

GRAPH_ME_URL = 'https://graph.microsoft.com/v1.0/me'
# openid+profile are required to get an ID token in principle, but this flow
# never reads one - email/User.Read is enough since the verified address is
# read back from Graph's own /me instead of parsed out of a token.
SCOPE = 'openid profile email User.Read'

STATE_SESSION_KEY = 'azure_auth_state'
NEXT_SESSION_KEY = 'azure_auth_next'


class AzureAuthError(Exception):
    """Raised when Microsoft sign-in could not be completed. The message is
    fit to show to the person signing in - never raises anything else."""


def is_configured():
    return bool(
        getattr(settings, 'AZURE_AD_TENANT_ID', '') and getattr(settings, 'AZURE_AD_CLIENT_ID', '')
        and getattr(settings, 'AZURE_AD_CLIENT_SECRET', ''))


def _authority():
    return f'https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}'


def build_auth_url(request, redirect_uri, next_url=''):
    """Starts the Authorization Code flow: stashes a random `state` (so the
    callback can be sure it's answering this same request, not a forged one)
    plus where to send the user once they're signed in, then returns the
    Microsoft `/authorize` URL to redirect the browser to."""
    state = secrets.token_urlsafe(24)
    request.session[STATE_SESSION_KEY] = state
    request.session[NEXT_SESSION_KEY] = next_url if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()) else ''

    params = {
        'client_id': settings.AZURE_AD_CLIENT_ID,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'response_mode': 'query',
        'scope': SCOPE,
        'state': state,
    }
    return f'{_authority()}/oauth2/v2.0/authorize?{urlencode(params)}'


def acquire_user_email(request, *, code, state, redirect_uri):
    """Validates `state`, exchanges `code` for an access token, and returns
    (email, next_url) for whoever just signed in - email is Microsoft
    Graph's own `mail`, falling back to `userPrincipalName` for guests whose
    mail attribute isn't set. Raises AzureAuthError on any failure; never
    raises anything else."""
    expected_state = request.session.pop(STATE_SESSION_KEY, None)
    next_url = request.session.pop(NEXT_SESSION_KEY, '')
    if not code or not state or not expected_state or not secrets.compare_digest(state, expected_state):
        raise AzureAuthError('Sign-in could not be verified - please try again.')

    data = {
        'client_id': settings.AZURE_AD_CLIENT_ID,
        'client_secret': settings.AZURE_AD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'scope': SCOPE,
    }
    try:
        response = requests.post(f'{_authority()}/oauth2/v2.0/token', data=data, timeout=15)
    except requests.RequestException as exc:
        raise AzureAuthError(f'Could not reach Microsoft to complete sign-in: {exc}')
    if response.status_code >= 400:
        logger.warning('Azure AD token exchange failed (HTTP %s): %s', response.status_code, response.text[:300])
        raise AzureAuthError('Microsoft sign-in failed - please try again.')

    try:
        access_token = response.json()['access_token']
    except (ValueError, KeyError):
        raise AzureAuthError('Microsoft returned an unexpected sign-in response.')

    try:
        me = requests.get(GRAPH_ME_URL, headers={'Authorization': f'Bearer {access_token}'}, timeout=15)
    except requests.RequestException as exc:
        raise AzureAuthError(f'Could not confirm your identity with Microsoft: {exc}')
    if me.status_code >= 400:
        logger.warning('Microsoft Graph /me failed (HTTP %s): %s', me.status_code, me.text[:300])
        raise AzureAuthError('Could not confirm your identity with Microsoft - please try again.')

    profile = me.json()
    email = profile.get('mail') or profile.get('userPrincipalName')
    if not email:
        raise AzureAuthError('Microsoft did not provide an email address for this account.')
    return email, next_url
