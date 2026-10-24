"""NewsPerz entry point.

`python app.py` runs Flask's dev server when FLASK_DEBUG is set, and Waitress
otherwise. For production use `waitress-serve --call 'backend:create_app'`
behind a proxy.
"""

import os

from backend import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    if os.environ.get('FLASK_DEBUG'):
        # use_reloader=False keeps the listener single, which avoids the
        # Windows Hyper-V dynamic-port collision on 5000.
        app.run(host='127.0.0.1', port=port, debug=True, use_reloader=False)
    else:
        try:
            from waitress import serve
        except ImportError:
            raise SystemExit('Waitress is not installed. Run: pip install -r requirements.txt')
        print(f'NewsPerz on http://127.0.0.1:{port} (waitress)')
        serve(app, host='127.0.0.1', port=port, threads=8)
