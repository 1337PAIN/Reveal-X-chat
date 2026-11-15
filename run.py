"""
Reveal-X: Secure Image Sharing Chat Application.

The **development** entry point. It reloads templates, prints the LAN URL and
fails loudly when the port is taken -- all useful while working, none of it what
should face a network.

Werkzeug's server needs allow_unsafe_werkzeug=True below to start at all under
Flask-SocketIO, which is the library stating plainly that it is not a production
server. For deployment use the Procfile (gunicorn); see docs/DEPLOYMENT.md.
"""

import os

from app import app, socketio


def bind_host():
    """The address to listen on: REVEALX_BIND_HOST, or every interface.

    Every interface is right for development on a LAN. Behind a tunnel it is
    not: the app then trusts forwarded headers (REVEALX_TRUST_PROXY), so
    anything else that can reach the port could forge a client address. Set
    REVEALX_BIND_HOST=127.0.0.1 and only the tunnel, running on this machine,
    can connect. See docs/DEPLOYMENT.md.
    """
    return os.environ.get('REVEALX_BIND_HOST', '').strip() or '0.0.0.0'


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '').lower() == 'true'

    print("=" * 50)
    print("REVEAL-X DEVELOPMENT SERVER STARTING")
    print("=" * 50)
    print("\nNot for deployment -- use the Procfile (gunicorn).")
    print("See docs/DEPLOYMENT.md")
    print("\nOpen your browser and go to:")
    print("   http://localhost:5000")
    print("\nShare this URL with others on your network:")
    print("   http://YOUR_IP:5000")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 50)

    port = int(os.environ.get('PORT', 5000))
    host = bind_host()
    use_ssl = 'adhoc' if os.environ.get('REVEAL_X_ADHOC_SSL', '').lower() == 'true' else None
    
    # Fail loudly if something already holds the port. Otherwise this process
    # initialises the database, fails to bind, exits -- and the older server
    # keeps answering with stale code, which is confusing to debug.
    import socket as _socket
    family = _socket.AF_INET6 if ':' in host else _socket.AF_INET
    probe = _socket.socket(family, _socket.SOCK_STREAM)
    try:
        probe.bind((host, port))
    except OSError:
        print("")
        print(f"ERROR: port {port} is already in use.")
        print("Another Reveal-X server is probably still running. Stop it first:")
        print("   Windows:  powershell \"Get-Process python | Stop-Process -Force\"")
        print("   Linux/macOS:  pkill -f run.py")
        raise SystemExit(1)
    finally:
        probe.close()

    socketio.run(app, debug=debug, host=host, port=port, ssl_context=use_ssl, allow_unsafe_werkzeug=True)
