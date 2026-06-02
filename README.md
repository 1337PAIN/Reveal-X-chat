# 🔒 Reveal-X: Secure Image Sharing & Chat

Reveal-X is a premium, real-time secure messaging web application. Its flagship feature is the integration of **Visual Cryptography** for secure, serverless image sharing, alongside modern security hardening and a highly responsive glassmorphic UI.

---

## 🌟 Key Features

*   **Visual Cryptography Image Sharing:**
    *   Images are securely split into two random noise "shares" (a 2-out-of-2 secret sharing scheme) on the sender's device.
    *   **Share 1** is transmitted securely over the network.
    *   **Share 2** is transmitted live and held in the receiver's local session memory.
    *   The image can ONLY be viewed when the receiver clicks "Reconstruct", combining both shares locally on their device using the HTML5 Canvas API. The server never sees or stores the original image.
*   **Real-Time Messaging:** Instant, low-latency text communication via WebSockets with typing indicators, read receipts, and live online status tracking.
*   **Security First Architecture:**
    *   Complete defense against Cross-Site Scripting (XSS) using `DOMPurify` and strict HTML escaping.
    *   Passwords are computationally hashed using the `scrypt` algorithm.
    *   **E2EE UI Mockup:** Demonstrates local ECDH public/private key generation via the WebCrypto API.
*   **Premium UX/UI:**
    *   Stunning dark-mode first design with glassmorphism (frosted glass) effects.
    *   Fully responsive, mobile-first layouts with smooth, native-feeling animations.
    *   WCAG 2.1 AA Accessibility compliance (Screen-reader announcements, keyboard navigation).
*   **Progressive Web App (PWA):** Fully installable on mobile and desktop, with service-worker caching for offline resilience.

---

## 🛠️ Technology Stack

*   **Backend:** Python, Flask, Flask-SocketIO
*   **Database:** SQLite (Default) / PostgreSQL (via migration scripts)
*   **Cryptography & Security:** Werkzeug (`scrypt`), WebCrypto API, DOMPurify
*   **Frontend:** Vanilla JavaScript, HTML5 Canvas API, Vanilla CSS (Variables, Glassmorphism)
*   **Deployment ready:** PWA Service Workers, `requirements.txt` included

---

## 🚀 Installation & Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/Reveal-X-chat.git
    cd Reveal-X-chat
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    # Windows
    python -m venv .venv
    .venv\Scripts\activate

    # macOS/Linux
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Run the application:**
    ```bash
    python run.py
    ```

5.  **Access the app:**
    Open your web browser and navigate to `http://127.0.0.1:5000`

---

## 📁 Project Structure

*   `/app` - Core Flask application (Routes, Models, WebSockets)
*   `/app/static` - PWA Manifest, Service Workers, CSS, and Vanilla JS (`chat.js`, `crypto.js`)
*   `/app/templates` - Jinja2 HTML templates
*   `/scripts` - Database migration utilities (SQLite to PostgreSQL)

---

## 🛡️ License
This project is for educational and demonstrative purposes in secure web development and cryptography.
