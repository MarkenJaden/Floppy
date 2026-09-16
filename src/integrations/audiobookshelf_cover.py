"""Signed-token helpers for the Audiobookshelf cover art proxy.

Audiobookshelf's ``/api/items/:id/cover`` endpoint requires a bearer token,
so the raw URL can never be embedded directly in an ``<img src>`` - the
browser has no way to attach the header. Floppy instead serves these covers
through its own authenticated proxy (see ``integrations.views.audiobookshelf_cover``),
identified by a signed token so the view never has to trust client input for
which account/item to fetch. This mirrors ``app.image_cache``'s provider
image proxy, but is scoped to a user's own Audiobookshelf account rather
than a fixed CDN allowlist, since ABS servers are arbitrary user-configured
hosts.
"""

import base64
import binascii

from django.conf import settings
from django.core.signing import BadSignature, Signer
from django.urls import get_script_prefix, reverse

SIGNER_SALT = "floppy.abs-cover"

# Kept in sync with the "import/audiobookshelf/cover/<str:token>" route.
PROXY_PATH_PREFIX = "/import/audiobookshelf/cover/"


def _signer():
    return Signer(salt=SIGNER_SALT)


def build_cover_proxy_url(account_id, library_item_id):
    """Return a Floppy-hosted URL that serves an ABS item's cover art."""
    payload = f"{account_id}:{library_item_id}"
    token = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    signed = _signer().sign(token)
    path = reverse(
        "audiobookshelf_cover", kwargs={"token": signed}, urlconf="config.urls"
    )
    # This always runs inside a Celery worker (even a manual "sync now" is
    # queued), whose ROOT_URLCONF is deliberately empty since it never serves
    # HTTP - hence the explicit urlconf above. Celery also never handles a
    # request, so the script-prefix thread-local reverse() applies stays at
    # its "/" default and never picks up a configured BASE_URL subpath the
    # way a real request would. Add it by hand only when that default is
    # still in effect, so a future request-context caller with the real
    # prefix already set isn't double-prefixed.
    if get_script_prefix() == "/" and settings.FORCE_SCRIPT_NAME:
        return settings.FORCE_SCRIPT_NAME.rstrip("/") + path
    return path


def resolve_cover_proxy_token(token):
    """Return (account_id, library_item_id) for a signed cover token, or None."""
    try:
        payload = _signer().unsign(token)
        decoded = base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8")
    except (BadSignature, binascii.Error, ValueError, UnicodeDecodeError):
        return None

    account_id, _, library_item_id = decoded.partition(":")
    if not account_id or not library_item_id:
        return None
    return account_id, library_item_id


def is_cover_proxy_url(url):
    """Return whether `url` is one of Floppy's own ABS cover proxy URLs."""
    if not isinstance(url, str) or not url:
        return False
    return PROXY_PATH_PREFIX in url
