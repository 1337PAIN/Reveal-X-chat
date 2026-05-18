"""
Reveal-X: Secure Image Sharing Chat Application.
Run this file to start the server.
"""

import os

from app import app, socketio


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG', '').lower() == 'true'

    print("=" * 50)
    print("REVEAL-X SERVER STARTING")
    print("=" * 50)
    print("\nOpen your browser and go to:")
    print("   http://localhost:5000")
    print("\nShare this URL with others on your network:")
    print("   http://YOUR_IP:5000")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 50)

    port = int(os.environ.get('PORT', 5000))
    use_ssl = 'adhoc' if not os.environ.get('PORT') else None
    
    socketio.run(app, debug=debug, host='0.0.0.0', port=port, ssl_context=use_ssl, allow_unsafe_werkzeug=True)
