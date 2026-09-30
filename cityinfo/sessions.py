"""Zyte API session management: logs in once per session (pinned browser
fingerprint/IP) and re-validates the session on every subsequent response.

Recon findings (see debug/ and README.md):
- Login form (#formLogin) submits via a jQuery $.ajax POST to
  /login/ajaxPopupLogin/. A plain (non-browser) POST through Zyte returns an
  empty body -- the endpoint appears to require a real browser context behind
  Cloudflare. Login via Zyte browserHtml + actions (type email, type
  password, click #submitLogin) does work and shows a "Successfully
  Loggedin" toast.
- Logged-out marker: header contains
  <div class="account"><a class="login">Login</a> / <a class="signup">...
  This div's "login"/"signup" anchors disappear once logged in.
"""
import logging

from scrapy import Request
from scrapy.http import Response
from scrapy_zyte_api import SessionConfig, is_session_init_request, session_config

logger = logging.getLogger(__name__)

LOGIN_PAGE_URL = "https://properties.cityinfoservices.com/signup/actvty/l/"


def check_login(response: Response) -> bool:
    """True if *response* was rendered for a logged-in session.

    The logged-out header shows `<div class="account"><a class="login">
    Login</a> / <a class="signup">Sign Up</a></div>`. Once logged in this
    anchor is replaced by account/logout controls, so its absence is our
    positive signal.
    """
    if not hasattr(response, "css"):
        return False
    login_anchor = response.css("div.account a.login")
    return len(login_anchor) == 0


@session_config(["properties.cityinfoservices.com"])
class CityInfoSessionConfig(SessionConfig):
    def params(self, request: Request):
        site_user = self.crawler.settings["SITE_USER"]
        site_pass = self.crawler.settings["SITE_PASS"]
        return {
            "url": LOGIN_PAGE_URL,
            "browserHtml": True,
            "actions": [
                {
                    "action": "waitForSelector",
                    "selector": {"type": "css", "value": "#userLoginEmail"},
                    "timeout": 15,
                },
                {
                    "action": "type",
                    "selector": {"type": "css", "value": "#userLoginEmail"},
                    "text": site_user,
                },
                {
                    "action": "type",
                    "selector": {"type": "css", "value": "#userLoginPassword"},
                    "text": site_pass,
                },
                {
                    "action": "click",
                    "selector": {"type": "css", "value": "#submitLogin"},
                },
                {"action": "waitForTimeout", "timeout": 5},
            ],
        }

    def check(self, response: Response, request: Request) -> bool:
        if is_session_init_request(request):
            # The login is an in-page AJAX call; the header account block
            # only reflects the new state after a client-side reload the
            # action sequence does not perform, so we check the transient
            # "Successfully Loggedin" toast instead of the header here.
            ok = "Successfully Loggedin" in response.text
            if ok:
                logger.info("Zyte session login succeeded (session init request).")
            else:
                logger.warning("Zyte session login FAILED (no success toast in response).")
            return ok
        ok = check_login(response)
        if not ok:
            logger.warning("Session lost login state on %s; will reinitialize.", request.url)
        return ok
