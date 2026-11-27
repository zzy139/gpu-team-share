from flask import Flask

from api.security.headers import IMMUTABLE_CACHE, SecurityHeaders


def _app():
    app = Flask(__name__)
    SecurityHeaders(app, running_local=False)

    @app.route('/_next/static/chunks/<name>')
    def chunk(name):
        return 'x' * 10

    @app.route('/')
    def index():
        return '<html></html>'

    @app.route('/api/x')
    def api():
        return {'a': 1}

    @app.route('/_next/static/missing')
    def missing():
        return 'no', 404

    return app


def test_hashed_assets_are_immutable_and_the_rest_revalidates():
    client = _app().test_client()
    assert client.get('/_next/static/chunks/518-af672e1fac728d54.js').headers['Cache-Control'] == IMMUTABLE_CACHE
    for path in ('/', '/api/x', '/_next/static/missing'):
        assert client.get(path).headers['Cache-Control'] == 'no-cache', path
