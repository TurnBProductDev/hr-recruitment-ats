"""Crash-proof {placeholder} substitution shared by the admin-editable AI
prompts (prompts/store.py) and email templates (email_templates/store.py).

Plain str.format() raises KeyError/IndexError on a typo'd or removed
placeholder - which would turn one bad admin edit into a hard failure on
every scoring call or every outbound email. This leaves an unrecognized
{token} in the output untouched instead, so a mistake is visibly wrong
rather than a crash.
"""
import re

_TOKEN_RE = re.compile(r'\{(\w+)\}')


def render(text, **context):
    return _TOKEN_RE.sub(lambda m: str(context.get(m.group(1), m.group(0))), text)
