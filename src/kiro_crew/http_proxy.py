"""Explicit HTTP proxy selection without HTTP-client credential discovery."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The Slack Web API base the slack_sdk clients call; their proxy is picked for it.
SLACK_API_URL = "https://slack.com/api/"


def resolve_proxy(url: str) -> str | None:
    """Select HTTPS then HTTP proxy, respecting the destination host's bypass list.

    Read environment and OS settings on each call. Passing the result explicitly
    lets HTTP clients keep their environment-driven netrc credential lookup off.
    """
    proxies, from_os = system_proxies()
    if from_os:
        proxy = usable_os_proxy(proxies)
    else:
        proxy = proxies.get("https") or proxies.get("http")
    if not proxy:
        return None
    return None if is_bypassed(url) else proxy


def usable_os_proxy(proxies: dict[str, str]) -> str | None:
    """The one proxy an OS-sourced map yields for every Slack leg, or ``None`` for direct.

    The Web API (``resolve_proxy``) and Socket Mode both read the OS map through
    this filter, so they take the same proxy or both go direct.
    """
    # From the OS settings, not the operator's environment. A Windows
    # `socks=` entry reaches here as socks4://, which the HTTP clients
    # cannot speak, so take the first usable one of https then http, and
    # connect direct when neither is. An environment value passes through
    # unchanged in resolve_proxy and the client refuses it itself.
    candidates = [proxies.get("https"), proxies.get("http")]
    usable = [p for p in candidates if p and _scheme(p) in _USABLE_PROXY_SCHEMES]
    if not usable:
        unusable = next((p for p in candidates if p), None)
        if unusable is not None:
            _warn_unusable_scheme(_scheme(unusable))
        return None
    return usable[0]


def proxy_environment() -> dict[str, str]:
    """The ``*_proxy`` environment, read once: the snapshot the OS phase takes."""
    return urllib.request.getproxies_environment()


def os_phase_needed(env: dict[str, str]) -> bool:
    """Whether resolution reads the OS settings: no proxy variable beyond ``NO_PROXY``."""
    return not set(env) - {"no"}


def os_bypassed(url: str, env: dict[str, str]) -> bool:
    """``is_bypassed`` for an *env* with no proxy variable beyond ``NO_PROXY``.

    Reads ``NO_PROXY`` from the snapshot and the OS list from the OS, never
    ``os.environ``, so a worker thread can run it while the loop writes it.
    """
    checks: list[Callable[[str], bool]] = []
    if env:
        checks.append(lambda h: bool(urllib.request.proxy_bypass_environment(h, env)))  # type: ignore[attr-defined]
    os_bypass = _os_bypass()
    if os_bypass is not None:
        checks.append(os_bypass)
    return _host_matches(url, checks)


def _resolve_from_os(url: str, env: dict[str, str]) -> str | None:
    proxy = usable_os_proxy(_os_proxies())
    if not proxy:
        return None
    return None if os_bypassed(url, env) else proxy


async def resolve_proxy_off_loop(url: str) -> str | None:
    """``resolve_proxy`` for async code: run in a worker thread, never on the loop.

    The OS bypass matcher behind ``is_bypassed`` can block on a DNS lookup
    (Windows ``proxy_bypass_registry``), which would stall the event loop. Every
    async caller resolves through here and hands the result to the client it
    builds; constructors do no resolution of their own.
    """
    # The environment is read here, on the loop that writes it. Only the OS
    # phase goes to the thread, with the snapshot, so it never iterates
    # os.environ while channel_config_writes changes it.
    env = proxy_environment()
    if not os_phase_needed(env):
        return resolve_proxy(url)
    return await asyncio.to_thread(_resolve_from_os, url, env)


#: The Slack proxy the loop resolved for the sync code ``slack_thread`` runs.
#: ``asyncio.to_thread`` copies context variables, so the worker reads it here
#: instead of iterating ``os.environ`` while the loop writes it.
_slack_proxy_for_thread: contextvars.ContextVar[tuple[str | None] | None] = contextvars.ContextVar(
    "kirocrew_slack_proxy_for_thread", default=None
)


async def slack_thread(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """``asyncio.to_thread`` for sync Slack code: the proxy is resolved first, on the loop."""
    token = _slack_proxy_for_thread.set((await resolve_proxy_off_loop(SLACK_API_URL),))
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    finally:
        _slack_proxy_for_thread.reset(token)


def slack_proxy() -> str | None:
    """The Slack Web API proxy: the one ``slack_thread`` resolved, else read now."""
    preset = _slack_proxy_for_thread.get()
    return preset[0] if preset is not None else resolve_proxy(SLACK_API_URL)


def slack_web_client(
    token: str, proxy: str | None, *, client_cls: Any = None, **kwargs: Any
) -> Any:
    """A slack_sdk Web API client that keeps *proxy* as given, ``None`` included.

    slack_sdk treats ``proxy=None`` as unset and reloads ``HTTPS_PROXY``, so a
    ``NO_PROXY`` bypass that resolved to "direct" would go through the proxy
    after all. Assigning ``proxy`` after construction keeps the decision.
    *client_cls* is ``AsyncWebClient`` unless a sync caller passes ``WebClient``.
    """
    if client_cls is None:
        from slack_sdk.web.async_client import AsyncWebClient as client_cls
    client = client_cls(token=token, proxy=proxy, **kwargs)
    client.proxy = proxy
    return client


def system_proxies() -> tuple[dict[str, str], bool]:
    """The proxy map every Slack and Telegram route reads, and whether it is the OS's.

    ``urllib.request.getproxies()``, except that an environment naming only
    ``NO_PROXY`` still yields the OS proxies: the stdlib would return
    ``{"no": ...}`` there and never read the OS settings.
    """
    env = urllib.request.getproxies_environment()
    if not env:
        return urllib.request.getproxies(), True
    if not set(env) - {"no"}:
        return _os_proxies(), True
    return urllib.request.getproxies(), False


def _os_proxies() -> dict[str, str]:
    """The Windows registry or macOS proxy settings, read past the environment."""
    reader = getattr(urllib.request, "getproxies_registry", None) or getattr(
        urllib.request, "getproxies_macosx_sysconf", None
    )
    return reader() if reader else {}


def _scheme(proxy: str) -> str:
    try:
        return urllib.parse.urlsplit(proxy).scheme.lower()
    except ValueError:
        # A malformed OS value such as `http://[` is unusable, not a crash.
        return ""


# The proxy schemes aiohttp and slack_sdk can use. The stdlib already turns a
# Windows `http=` or `https=` entry into an http:// URL.
_USABLE_PROXY_SCHEMES = frozenset({"http", "https"})
_warned_schemes: set[str] = set()


def _warn_unusable_scheme(scheme: str) -> None:
    if scheme in _warned_schemes:
        return
    _warned_schemes.add(scheme)
    logger.warning(
        "Ignoring a %s:// system proxy: the HTTP clients cannot use it, so they connect direct",
        scheme or "schemeless",
    )


def is_bypassed(url: str) -> bool:
    """True when the bypass list names *url*'s host, bare or as ``host:port``.

    The port is the URL's own or its scheme's default. ``resolve_proxy`` and
    Slack Socket Mode use this rule, which reads the OS bypass list too. A
    caller on the event loop uses ``proxy_for_url``, which reads ``NO_PROXY``
    alone because the OS fallback can block on a DNS lookup.
    """
    checks: list[Callable[[str], bool]] = [urllib.request.proxy_bypass]
    if set(urllib.request.getproxies_environment()) == {"no"}:
        # NO_PROXY alone: system_proxies() hands out the OS proxy, but the
        # stdlib's proxy_bypass then reads NO_PROXY only, so add the OS list.
        os_bypass = _os_bypass()
        if os_bypass is not None:
            checks.append(os_bypass)
    return _host_matches(url, checks)


def _host_matches(url: str, checks: list[Callable[[str], bool]]) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return False
    default_port = 443 if parsed.scheme in ("https", "wss") else 80
    try:
        port = parsed.port or default_port
    except ValueError:
        # An out-of-range port must not make the shared check raise; fall back
        # to the scheme default so the bare-host check still runs.
        port = default_port
    return any(check(h) for check in checks for h in (host, f"{host}:{port}"))


def _os_bypass() -> Callable[[str], bool] | None:
    """The Windows registry or macOS bypass matcher, or None on other platforms."""
    return getattr(urllib.request, "proxy_bypass_registry", None) or getattr(
        urllib.request, "proxy_bypass_macosx_sysconf", None
    )


def proxy_for_url(proxy: str | None, url: str) -> str | None:
    """Return *proxy* for *url*, or ``None`` when ``NO_PROXY`` lists its host.

    Reads only the environment's bypass list, never OS proxy settings, so it is
    cheap enough to call per request on the event loop.
    """
    if not proxy:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
    except ValueError:
        return proxy
    host = parsed.hostname or ""
    if not host:
        return proxy
    if port is None:
        port = 443 if parsed.scheme in ("https", "wss") else 80
    # ``host:port`` matches an entry naming the bare host or that exact port.
    if urllib.request.proxy_bypass_environment(f"{host}:{port}"):  # type: ignore[attr-defined]
        return None
    return proxy
