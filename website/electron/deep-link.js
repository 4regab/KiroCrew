// kirocrew:// deep links for the Kiro Crew Electron app.
//
// Other programs on the machine (a companion app, a Slack button, a Raycast
// script, `kirocrew open`) use this scheme to open the desktop app on one
// session rather than only bring its window forward. A link carries the same
// target as the dashboard's browser deep link, `/chat?sid=<slot key>`, and is
// delivered through the same `navigate` IPC the Crew Companion overlay's "Open
// session" uses inside the process.
//
// This module owns the one decision that matters for a scheme handler: which
// URLs are allowed to drive navigation. The string comes from the OS, which
// hands the app whatever any other program asked LaunchServices (or the
// Windows/Linux registration) to open, so it is untrusted input. The rule is an
// allowlist of three shapes -- raise the window, open one session, or open one
// session on a remote crew -- mirroring `external-scheme.js`'s stance on
// outbound schemes: a deny-by-default shape, never a pass-through.
//
// Electron-free on purpose (like display-media.js and external-scheme.js) so
// the parsing and the allowlist are unit-tested without a live app;
// deep-link-handler.js and main.js own the Electron wiring and the window raise.
"use strict";

/** The scheme this app registers. Also the value in package.json `build.protocols`. */
const SCHEME = "kirocrew";

/**
 * Pick the kirocrew:// URL out of a process argv, if one is there.
 *
 * On Windows and Linux the OS launches a SECOND instance with the URL as an
 * argument; Electron's `second-instance` event hands the primary instance that
 * argv. On macOS the URL arrives through `open-url` instead and argv carries
 * none, so this answers "" there. Only the LAST matching argument is taken: a
 * second URL on one command line is not a thing the OS produces, and taking
 * the first would let a crafted launch smuggle an earlier argument past a
 * caller that only looked at the end.
 *
 * @param {ReadonlyArray<string>} argv the raw argv (first entry is the binary).
 * @returns {string} the URL, or "" when none is present.
 */
function deepLinkFromArgv(argv) {
  if (!Array.isArray(argv)) return "";
  let found = "";
  for (const arg of argv) {
    if (typeof arg === "string" && arg.toLowerCase().startsWith(SCHEME + ":")) {
      found = arg;
    }
  }
  return found;
}

/**
 * Resolve a kirocrew:// URL to the dashboard route it may open.
 *
 * Exactly three forms are accepted:
 *
 *   kirocrew://                              -> { route: "" }           (raise the window)
 *   kirocrew://chat?sid=<slot key>           -> { route: "/chat?sid=<encoded key>", slotKey }
 *   kirocrew://chat?crew=<crew id>&sid=<key> -> { route: "/chat?crew=<id>&crewSid=<encoded key>", slotKey }
 *
 * A crew link's route carries no `sid`, so an older dashboard never opens a
 * same-named LOCAL session. A `crew` that is not a crew id routes to the
 * value-free `/chat?crewError=1`, so the dashboard can say so.
 *
 * Anything else -- another scheme, an unknown host, a path segment, a missing
 * or malformed sid, any other query parameter -- is refused with a reason. The
 * route is REBUILT from the parsed pieces, never copied from the input, so the
 * only characters that can reach `navigate` are the ones the sid check admits.
 * A token never rides a deep link: the desktop window is already authenticated,
 * and a credential in a URL that any program on the machine can hand to
 * LaunchServices would be a credential in every program's reach.
 *
 * `sid` is passed through `encodeURIComponent` after a length and character
 * check: slot keys are the gateway's own identifiers (`cos-quick-chat`,
 * `chat-3-1785870239`, `slack_1712345678.123456`), so a conservative class is
 * enough and anything outside it is refused rather than escaped.
 *
 * @param {string} raw the URL as the OS delivered it.
 * @returns {{ok: true, route: string, slotKey: string} | {ok: false, reason: string}}
 */
function resolveDeepLink(raw) {
  if (typeof raw !== "string" || !raw) return refuse("empty");
  let url;
  try {
    url = new URL(raw);
  } catch {
    return refuse("unparseable");
  }
  if (url.protocol !== SCHEME + ":") return refuse("wrong scheme");
  if (url.username || url.password || url.port) return refuse("credentials or port in URL");
  if (url.hash) return refuse("fragment not allowed");

  // `new URL("kirocrew://chat?sid=x")` puts "chat" in hostname and "" in
  // pathname. A bare `kirocrew://` (or `kirocrew:`) has an empty hostname.
  const host = url.hostname.toLowerCase();
  const segments = url.pathname.split("/").filter(Boolean);
  const params = [...url.searchParams.keys()];

  if (!host) {
    if (segments.length || params.length) return refuse("unknown route");
    return { ok: true, route: "", slotKey: "" };
  }

  if (host !== "chat" || segments.length) return refuse("unknown route");
  if (params.length === 0) return refuse("missing sid");
  const crewLink = params.length === 2 && params.includes("crew") && params.includes("sid");
  if (!crewLink && (params.length !== 1 || params[0] !== "sid")) return refuse("unknown query parameter");
  const sid = url.searchParams.get("sid") || "";
  if (!crewLink) {
    if (!isSlotKey(sid)) return refuse("invalid sid");
    return { ok: true, route: `/chat?sid=${encodeURIComponent(sid)}`, slotKey: sid };
  }
  if (!CREW_SLOT_KEY_RE.test(sid)) return refuse("invalid sid");
  const crew = url.searchParams.get("crew") || "";
  if (!CREW_ID_RE.test(crew)) return { ok: true, route: "/chat?crewError=1", slotKey: "" };
  return { ok: true, route: `/chat?crew=${crew}&crewSid=${encodeURIComponent(sid)}`, slotKey: sid };
}

function refuse(reason) {
  return { ok: false, reason };
}

// Gateway slot keys: letters, digits, and the separators the gateway itself
// uses. 200 is well above any real key and far below anything that could
// matter to a URL or an IPC payload. `cli_open.py` carries the same pattern.
const SLOT_KEY_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;

function isSlotKey(s) {
  return typeof s === "string" && SLOT_KEY_RE.test(s);
}

// A crew link's key is also stored as that crew's last-chat hint, whose store
// takes at most 128 characters (`isPaneSlotKey`, website/src/lib/paneLastSlot.ts),
// so the slot-key rule above is capped there. A test pins both.
const CREW_SLOT_KEY_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
// A crew id as the instances registry mints it (`_ID_RE`,
// src/kiro_crew/instances/registry.py; a test pins the two).
const CREW_ID_RE = /^[a-z0-9][a-z0-9-]{0,62}$/;

module.exports = {
  SCHEME,
  deepLinkFromArgv,
  resolveDeepLink,
};
