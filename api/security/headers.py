from flask import Response
from flask.scaffold import Scaffold
from flask_cors import CORS

CORP_HEADERS = [
    {'key': 'Cross-Origin-Resource-Policy', 'default': 'same-site'},
    {'key': 'Cross-Origin-Embedder-Policy', 'default': 'require-corp'}
]

SECURITY_HEADERS = [
    {'key': 'X-Frame-Options', 'default': 'DENY'},
    {'key': 'X-Content-Type-Options', 'default': 'nosniff'},
    {'key': 'Referrer-Policy', 'default': 'strict-origin-when-cross-origin'},
    {'key': 'Strict-Transport-Security', 'default': 'max-age=63072000; includeSubDomains; preload'},
    {'key': 'Permissions-Policy', 'default': 'interest-cohort=()'},
    {'key': 'X-XSS-Protection', 'default': '1; mode=block'}
]

CSP_HEADER = {
    'key': 'Content-Security-Policy',
    'default': "default-src 'self'; style-src 'self' 'unsafe-inline'; font-src data:; img-src 'self' data:; child-src blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; upgrade-insecure-requests;"
}

IMMUTABLE_PREFIX = '/_next/static/'
IMMUTABLE_CACHE = 'public, max-age=31536000, immutable'


def add_cache_headers(response: Response):
    """Hashed Next.js build assets never change under the same URL: let browsers keep them for a year. Everything
    else (index.html, API and team-quota JSON) is revalidated on every request."""
    from flask import request
    path = request.path or ''
    if response.status_code == 200 and path.startswith(IMMUTABLE_PREFIX) and request.method in ('GET', 'HEAD'):
        response.headers['Cache-Control'] = IMMUTABLE_CACHE
    else:
        response.headers['Cache-Control'] = 'no-cache'
    return response


def add_security_headers(response: Response):
    for header in [*CORP_HEADERS, *SECURITY_HEADERS, CSP_HEADER]:
        response.headers.setdefault(**header)
    return response


def add_security_headers_dev(response: Response):
    for header in SECURITY_HEADERS:
        response.headers.setdefault(**header)
    return response


class SecurityHeaders(object):

    def __init__(self, app: Scaffold = None, running_local=False):
        self.running_local = running_local
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Scaffold):
        if self.running_local:
            CORS(app)
            app.after_request(add_security_headers_dev)
        else:
            app.after_request(add_security_headers)
            app.after_request(add_cache_headers)
