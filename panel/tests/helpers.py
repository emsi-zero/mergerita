from importlib import import_module

from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest
from django.test import Client

from panel.middleware import mark_panel_session


def panel_login(client: Client, user: AbstractBaseUser) -> None:
    """Sign `user` in to the panel session of `client`.

    `force_login` writes only the admin cookie, so this builds the session itself.
    """
    request = HttpRequest()
    request.session = import_module(settings.SESSION_ENGINE).SessionStore()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    mark_panel_session(request)
    request.session.save()
    client.cookies[settings.PANEL_SESSION_COOKIE_NAME] = request.session.session_key
