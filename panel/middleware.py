import time

from django.conf import settings
from django.contrib.sessions.backends.base import UpdateError
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers
from django.utils.http import http_date

PANEL_PREFIX = "/panel/"
AREA_KEY = "_rmo_area"
PANEL_AREA = "panel"


def _cookie_name_and_path(request: HttpRequest) -> tuple[str, str]:
    """Return the session cookie name and path for the URL area (INV-1)."""
    if request.path_info.startswith(PANEL_PREFIX):
        return settings.PANEL_SESSION_COOKIE_NAME, PANEL_PREFIX
    return settings.SESSION_COOKIE_NAME, settings.SESSION_COOKIE_PATH


def mark_panel_session(request: HttpRequest) -> None:
    """Mark the request's session as a panel session; call it after `login()`."""
    request.session[AREA_KEY] = PANEL_AREA


class AreaSessionMiddleware(SessionMiddleware):
    """Use a session cookie of its own on panel URLs (PAU-1, PAU-2, PAU-3).

    `process_response` follows Django 5.2 `SessionMiddleware`; only the cookie
    name and path differ. A session that carries the panel marker opens only
    panel URLs, and a session without it opens only other URLs, so a session key
    copied into the other area's cookie signs no one in.
    """

    def process_request(self, request: HttpRequest) -> None:
        name, _ = _cookie_name_and_path(request)
        session = self.SessionStore(request.COOKIES.get(name))
        is_panel_url = request.path_info.startswith(PANEL_PREFIX)
        if (session.get(AREA_KEY) == PANEL_AREA) != is_panel_url:
            session = self.SessionStore()
        request.session = session

    def process_response(
        self, request: HttpRequest, response: HttpResponse
    ) -> HttpResponse:
        try:
            accessed = request.session.accessed
            modified = request.session.modified
            empty = request.session.is_empty()
        except AttributeError:
            return response
        name, path = _cookie_name_and_path(request)
        if name in request.COOKIES and empty:
            response.delete_cookie(
                name,
                path=path,
                domain=settings.SESSION_COOKIE_DOMAIN,
                samesite=settings.SESSION_COOKIE_SAMESITE,
            )
            need_vary_cookie = True
        else:
            need_vary_cookie = accessed
            if (modified or settings.SESSION_SAVE_EVERY_REQUEST) and not empty:
                if request.session.get_expire_at_browser_close():
                    max_age = None
                    expires = None
                else:
                    max_age = request.session.get_expiry_age()
                    expires = http_date(time.time() + max_age)
                if response.status_code < 500:
                    try:
                        request.session.save()
                    except UpdateError as error:
                        raise SessionInterrupted(
                            "The request's session was deleted before the "
                            "request completed. The user may have logged "
                            "out in a concurrent request, for example."
                        ) from error
                    response.set_cookie(
                        name,
                        request.session.session_key,
                        max_age=max_age,
                        expires=expires,
                        domain=settings.SESSION_COOKIE_DOMAIN,
                        path=path,
                        secure=settings.SESSION_COOKIE_SECURE or None,
                        httponly=settings.SESSION_COOKIE_HTTPONLY or None,
                        samesite=settings.SESSION_COOKIE_SAMESITE,
                    )
                    need_vary_cookie = True
        if need_vary_cookie:
            patch_vary_headers(response, ("Cookie",))
        return response
