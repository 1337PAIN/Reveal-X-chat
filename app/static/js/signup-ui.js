/**
 * Reveal-X: the rules of the "Create account" form.
 *
 * Sign-in and sign-up shared one username and password and two buttons, and
 * a name that was already taken got "Request submitted" while nothing was
 * created. The form now has a Create account mode: the name is checked as it
 * is typed (check_username on the server), the answer is shown under the box,
 * and the password is typed twice. The wording is here, where
 * tests/js/signup-ui.test.mjs checks it; chat/auth.js puts it on the page.
 */

(function () {
    const USERNAME = /^[A-Za-z0-9_.-]{3,30}$/;

    /** What to say under the username box, from the server's username_status. */
    function usernameHint(status) {
        if (!status) return { text: '', state: 'idle' };
        if (status.available) return { text: `${status.username} is available`, state: 'ok' };
        return { text: status.error || 'That username cannot be used.', state: 'bad' };
    }

    /** Whether a name is worth asking the server about yet. */
    function worthChecking(name) {
        return USERNAME.test(String(name || '').trim());
    }

    /**
     * The first thing wrong with the form, or '' when it can be sent.
     * `status` is the last username_status for this exact name, if any.
     */
    function signupProblem({ username, password, confirm, status }) {
        const name = String(username || '').trim();
        if (!USERNAME.test(name)) return 'Username must be 3-30 letters, numbers, dots, dashes, or underscores';
        if (status && status.username === name && !status.available) return status.error;
        if (!password || password.length < 8) return 'Password must be at least 8 characters';
        if (password.toLowerCase() === name.toLowerCase()) return 'Password cannot match username';
        if (password !== confirm) return 'The two passwords do not match';
        return '';
    }

    window.revealxSignupUI = { usernameHint, worthChecking, signupProblem };
})();
