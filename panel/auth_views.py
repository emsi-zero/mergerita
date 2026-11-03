import posixpath
from urllib.parse import quote, unquote, urlsplit

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from panel.middleware import PANEL_PREFIX, mark_panel_session


def _safe_next(request: HttpRequest) -> str:
    """Return the normalized `next` path when it stays on the panel, else the home."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return PANEL_PREFIX
    path = unquote(urlsplit(target).path)
    normalized = posixpath.normpath(path)
    if path.endswith("/") and not normalized.endswith("/"):
        normalized += "/"
    if normalized == PANEL_PREFIX.rstrip("/") or normalized.startswith(PANEL_PREFIX):
        return quote(normalized)
    return PANEL_PREFIX


@method_decorator(sensitive_post_parameters("password"), name="dispatch")
@method_decorator(never_cache, name="dispatch")
class PanelLoginView(View):
    """Show the panel sign-in page and accept a local sign-in (PAU-4)."""

    http_method_names = ["get", "post"]

    def get(self, request: HttpRequest) -> HttpResponse:
        """Show the sign-in page for the enabled modes."""
        return self._render(request, AuthenticationForm(request))

    def post(self, request: HttpRequest) -> HttpResponse:
        """Sign in a local user, or reply 404 when `local` is off."""
        if "local" not in settings.PANEL_AUTH_MODES:
            raise Http404
        form = AuthenticationForm(request, data=request.POST)
        if not form.is_valid():
            return self._render(request, form)
        login(request, form.get_user())
        mark_panel_session(request)
        return redirect(_safe_next(request))

    def _render(self, request: HttpRequest, form: AuthenticationForm) -> HttpResponse:
        context = {
            "form": form,
            "next": _safe_next(request),
            "local_enabled": "local" in settings.PANEL_AUTH_MODES,
            "sso_enabled": "sso" in settings.PANEL_AUTH_MODES,
        }
        return render(request, "panel/login.html", context)


class PanelLogoutView(View):
    """End the panel session only (PAU-3)."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        """Sign out and go to the sign-in page."""
        logout(request)
        return redirect(reverse("panel:login"))


class SsoStubView(View):
    """Reply that GitLab sign-in is not set up. Never signs in (INV-2, PAU-5)."""

    http_method_names = ["get"]

    def get(self, request: HttpRequest) -> HttpResponse:
        """Render the not-set-up page with 501, or 404 when `sso` is off."""
        if "sso" not in settings.PANEL_AUTH_MODES:
            raise Http404
        return render(request, "panel/sso_not_configured.html", status=501)


class SsoStartView(SsoStubView):
    """Start of the GitLab sign-in (stub)."""


class SsoCallbackView(SsoStubView):
    """Return from GitLab (stub)."""
