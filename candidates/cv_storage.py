"""SAS-token access to CVs filed in Azure Blob Storage.

New candidates from the careers-mailbox intake flow get their CV written to
this container (see logic_apps/mailbox_intake.json's Create_blob action)
*alongside* the existing SharePoint copy - SharePoint stays as the filing
system of record, the blob is what this app actually renders. The container
has no public/anonymous access, so `Candidate.resume_url` stores the plain
blob URL (no token); this module turns that into a short-lived, read-only
URL on demand. The account key never leaves the server.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

from django.conf import settings

try:
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas
except ImportError:  # azure-storage-blob not installed - treat as unconfigured
    BlobSasPermissions = None
    generate_blob_sas = None


def is_configured():
    return bool(
        generate_blob_sas
        and settings.AZURE_STORAGE_ACCOUNT_NAME
        and settings.AZURE_STORAGE_ACCOUNT_KEY
    )


def _blob_host():
    return f"{settings.AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"


def is_our_blob_url(url):
    """True if `url` points at this app's own CV container, as opposed to an
    external link (a legacy SharePoint sharing URL)."""
    if not (url and is_configured()):
        return False
    return urlparse(url).hostname == _blob_host()


def sas_url(blob_url, minutes=15):
    """Turn a stored (unsigned) blob URL into a short-lived, read-only SAS
    URL. Caller must have already checked is_our_blob_url(blob_url)."""
    container, blob_name = urlparse(blob_url).path.lstrip('/').split('/', 1)
    token = generate_blob_sas(
        account_name=settings.AZURE_STORAGE_ACCOUNT_NAME,
        container_name=unquote(container),
        blob_name=unquote(blob_name),
        account_key=settings.AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=minutes),
    )
    return f"{blob_url}?{token}"
