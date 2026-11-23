"""Record cookies inherited from before a case or chain, and replay them in isolation."""

from __future__ import annotations

from http.cookiejar import Cookie, DefaultCookiePolicy
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from api_parity.models import RequestCase, ResponseCase


class StoredCookie(BaseModel):
    """The fields CookieJar uses to select and serialize an original cookie."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    order: int
    name: str
    value: str | None
    domain: str
    path: str
    secure: bool = False
    expires: int | None = None
    version: int = 0
    port: str | None = None
    port_specified: bool = False
    domain_specified: bool = False
    domain_initial_dot: bool = False
    path_specified: bool = True

    @classmethod
    def capture(cls, cookie: Cookie, order: int) -> StoredCookie:
        return cls(order=order, **{k: getattr(cookie, k) for k in cls.model_fields if k != "order"})

    def to_cookie(self) -> Cookie:
        return Cookie(**self.model_dump(exclude={"order"}), discard=self.expires is None,
                      comment=None, comment_url=None, rest={})


class SessionUse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: str | None = None
    auth_denied: bool | None = None
    cookies: list[StoredCookie] | None = None  # Withheld from the artifact when omitted.


SessionReplay = Literal["restored", "configured", "omitted", "ignored"]
TargetSide = Literal["target_a", "target_b"]
SessionMap = dict[TargetSide, SessionUse]


def _origin(url: httpx.URL) -> str:
    return str(url.copy_with(username=None, password=None, raw_path=b"/", fragment=None))


class _SelectionPolicy(DefaultCookiePolicy):
    """Let CookieJar enforce its normal path, domain, Secure and expiry rules."""

    def __init__(self, external: dict[Cookie, int], allowed: set[Cookie] | None):
        super().__init__()
        self.external = external
        self.allowed = allowed
        self.used: list[Cookie] = []

    def return_ok(self, cookie, request):
        if not super().return_ok(cookie, request):
            return False
        if cookie in self.external:
            if self.allowed is not None and cookie not in self.allowed:
                return False
            self.used.append(cookie)
        return True


class BundleCookies:
    """Per-target state for one bundle. New cookies within a replayed chain stay live."""

    def __init__(self, jar: httpx.Cookies, side: TargetSide, replay: bool, ignore: bool):
        self.jar = jar
        self.side = side
        self.replay = replay
        self.ignore = ignore
        self.external = {cookie: i for i, cookie in enumerate(jar.jar)}
        self.seeds: dict[StoredCookie, Cookie] = {}

    @classmethod
    def for_cases(cls, client: httpx.Client, cases: list[RequestCase], side: TargetSide,
                  ignore: bool = False) -> BundleCookies | None:
        if all(case.session_cookies is None for case in cases):
            return cls(client.cookies, side, False, False) if any(client.cookies.jar) else None

        session = cls(httpx.Cookies(), side, True, ignore)
        if ignore:
            return session
        # Seed once at the bundle boundary. A later Set-Cookie can replace or
        # delete this exact entry, even before its first recorded use.
        seeds = set()
        for case in cases:
            use = (case.session_cookies or {}).get(side)
            if use is not None and use.cookies:
                seeds.update(use.cookies)
        for stored in sorted(seeds, key=lambda c: c.order):
            cookie = stored.to_cookie()
            session.seeds[stored] = cookie
            session.external[cookie] = stored.order
            session.jar.jar.set_cookie(cookie)
        return session

    def prepare(self, client: httpx.Client, case: RequestCase,
                prepared: httpx.Request) -> tuple[str, list[Cookie], bool]:
        origin = _origin(prepared.url)
        explicit = "cookie" in client.headers or any(
            name.lower() == "cookie" and values for name, values in case.headers.items()
        )
        if explicit:
            return origin, [], True
        # HTTPX build_request merged the long-lived client's jar. Replace only
        # that header, using the same CookieJar merge and selection as HTTPX.
        prepared.headers.pop("cookie", None)
        cookies = httpx.Cookies(self.jar)
        cookies.update(case.cookies)
        allowed = None
        if self.replay:
            use = (case.session_cookies or {}).get(self.side)
            allowed = set()
            if not self.ignore and use is not None and use.origin == origin:
                allowed = {self.seeds[c] for c in use.cookies or ()}
        policy = _SelectionPolicy(self.external, allowed)
        cookies.jar.set_policy(policy)
        cookies.set_cookie_header(prepared)
        return origin, policy.used, False

    def finish(self, case: RequestCase, response: ResponseCase, origin: str,
               used: list[Cookie], explicit: bool) -> None:
        saved = (case.session_cookies or {}).get(self.side)
        denied = response.status_code in (401, 403)
        if used:
            response._session_cookie = SessionUse(
                origin=origin, auth_denied=denied,
                cookies=[StoredCookie.capture(cookie, self.external[cookie]) for cookie in used],
            )
        if saved is None:
            return
        if self.ignore:
            state = "ignored"
        elif explicit:
            state = "configured"
            response._session_cookie = SessionUse(origin=origin, auth_denied=denied)
        elif saved.cookies and all(self.seeds[c] in used for c in saved.cookies):
            state = "restored"
        else:
            state = "omitted"
            # Replaying a rewritten mismatch must still know about the missing prerequisite.
            response._session_cookie = saved.model_copy(update={"cookies": None})
        response.session_replay = state
        response.session_auth_rejected = state in ("restored", "configured") and denied and not saved.auth_denied
