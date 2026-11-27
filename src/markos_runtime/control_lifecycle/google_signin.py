"""Google sign-in for the issuer: the authorization code flow with PKCE and an ID token check.

The issuer alone holds the OAuth client secret. A configured map from Google subject ID to
MarkOS subject decides who may sign in. An email address never grants access. Errors never hold
a code, a token or a response body.
"""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import jwt  # type: ignore[import-not-found]

from markos_runtime.api.application import GOOGLE_CALLBACK_PATH
from markos_runtime.api.identity import IdentityDenied, IdentityUnavailable
from markos_runtime.api.mail_alerts import GOOGLE_ISSUERS, LEEWAY_SECONDS, SigningKeys
from markos_runtime.mail.google import GoogleEndpoints, GoogleError, OAuthClient, _call

SCOPE = "openid email"
CALLBACK_PATH = GOOGLE_CALLBACK_PATH
MAX_ID_TOKEN_CHARS = 4096
# Google's codes look like "4/0Ab...": letters, digits and a few separators.
CODE_PATTERN = re.compile(r"^[A-Za-z0-9/._-]{1,512}$")
GOOGLE_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


class GoogleSignIn:
    def __init__(
        self,
        oauth: OAuthClient,
        http: Any,
        endpoints: GoogleEndpoints,
        keys: SigningKeys,
        redirect_uri: str,
        subjects: Mapping[str, str],
    ) -> None:
        if not subjects or not all(GOOGLE_SUBJECT.fullmatch(k) for k in subjects):
            raise ValueError("explicit Google subject map required")
        self._oauth, self._http, self.endpoints = oauth, http, endpoints
        self._keys, self.redirect_uri = keys, redirect_uri
        self._subjects = dict(subjects)

    def authorization_url(self, state: str, code_challenge: str, nonce: str) -> str:
        params = {
            "client_id": self._oauth.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return self.endpoints.authorize + "?" + urlencode(params)

    async def sign_in(self, code: str, verifier: str) -> tuple[str, str]:
        """Exchanges Google's code. Returns the MarkOS subject and the ID token's nonce."""
        if not CODE_PATTERN.fullmatch(code):
            raise IdentityDenied("Google sign-in denied")
        try:
            status, body = await _call(
                self._http,
                "sign-in exchange",
                "POST",
                self.endpoints.token,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self._oauth.client_id,
                    "client_secret": self._oauth.client_secret,
                },
            )
        except GoogleError:
            raise IdentityUnavailable("Google sign-in unavailable") from None
        if 400 <= status < 500:
            raise IdentityDenied("Google sign-in denied")
        if status != 200:
            raise IdentityUnavailable("Google sign-in unavailable")
        token = body.get("id_token")
        if not isinstance(token, str) or not 0 < len(token) <= MAX_ID_TOKEN_CHARS:
            raise IdentityDenied("Google sign-in denied")
        try:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if header.get("alg") != "RS256" or not isinstance(kid, str):
                raise jwt.InvalidTokenError
            claims = jwt.decode(
                token,
                await self._keys.key(kid),
                algorithms=["RS256"],
                audience=self._oauth.client_id,
                issuer=GOOGLE_ISSUERS,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            )
        except jwt.PyJWKClientConnectionError:
            raise IdentityUnavailable("Google keys unavailable") from None
        except jwt.PyJWTError:
            raise IdentityDenied("Google sign-in denied") from None
        subject, nonce = claims.get("sub"), claims.get("nonce")
        if (
            claims.get("azp", self._oauth.client_id) != self._oauth.client_id
            or not isinstance(subject, str)
            or not isinstance(nonce, str)
            or subject not in self._subjects
        ):
            raise IdentityDenied("Google sign-in denied")
        return self._subjects[subject], nonce
