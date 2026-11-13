/**
 * Late-bound references between the chat modules.
 *
 * chat.js was one classic script, so a function anywhere in it could call one
 * declared further down. As modules, an import of a later module would make it
 * evaluate first and reorder the start-up code. Instead a module calls a later
 * module's function as late.<name>, and that module registers it here when it
 * loads. Every such call happens in a handler, after the page has loaded --
 * the split tool checked that none runs during start-up.
 */
export const late = {};

export function registerLate(functions) {
    Object.assign(late, functions);
}
