/**
 * Reveal-X chat client: the entry point.
 *
 * This was one 3,800-line classic script, chat.js. It is now these modules,
 * loaded natively by the browser (no bundler), in the same order the sections
 * of chat.js ran. Each module imports only from modules above it, so start-up
 * still runs top to bottom; calls the other way go through late.js.
 */
import './core.js';
import './basics.js';
import './session.js';
import './keys.js';
import './utils.js';
import './auth.js';
import './reactions.js';
import './presence.js';
import './render.js';
import './messaging.js';
import './images.js';
import './reconstruction.js';
import './settings.js';
import './shell.js';
