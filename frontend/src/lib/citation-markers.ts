/**
 * Shared citation-marker regexes — COPIED VERBATIM from CONTRACT.md §3.
 *
 * Rev 3's silent bug was the CLIENT regex diverging from the SERVER's. Both
 * sides copy these patterns from `app.config.settings`; NEITHER re-authors them.
 * Do not "improve" or refactor the pattern strings below — a divergent client
 * regex is the exact class of bug this file guards against.
 *
 * The server source of truth (Python, `app/config.py`) is:
 *
 *   MARKER_RE         \[\s*([CFT]\d{1,3}(?:\s*,\s*[CFT]\d{1,3})*)\s*\]
 *   PARTIAL_MARKER_RE \[[CFT]?\d{0,3}$        # dangling marker mid-stream
 *   MAX_HOLD          24                        # hold-back buffer (chars)
 *
 * Markers: `C` = citation, `F` = figure, `T` = table.
 * `[Figure 2]` is scrubbed to the literal text "Figure 2" — it must NEVER
 * become `[F2]`. Because MARKER_RE requires a `[CFT]` prefix immediately after
 * the bracket, `[1,2]`, `arr[1]`, `[text](url)` and `[Figure 2]` do not match.
 */

/** Pattern strings — the single verbatim source, so a RegExp can be minted fresh. */
export const MARKER_PATTERN = "\\[\\s*([CFT]\\d{1,3}(?:\\s*,\\s*[CFT]\\d{1,3})*)\\s*\\]";
export const PARTIAL_MARKER_PATTERN = "\\[[CFT]?\\d{0,3}$";

/** Hold-back buffer size (chars) for the mid-stream MarkerFilter (server P2). */
export const MAX_HOLD = 24;

/**
 * Mint a FRESH global RegExp per call. A module-level `/g` RegExp carries
 * `lastIndex` across invocations and skips matches nondeterministically during
 * streaming re-renders — never share a stateful global instance.
 */
export function newMarkerRe(): RegExp {
  return new RegExp(MARKER_PATTERN, "g");
}

/** Fresh RegExp matching a dangling marker at the END of a string (`$`-anchored). */
export function newPartialMarkerRe(): RegExp {
  return new RegExp(PARTIAL_MARKER_PATTERN);
}

/**
 * Split a marker group like "C3" or "C1, F2, T4" into individual marker tokens.
 * The group is MARKER_RE capture group 1 (already validated to be well-formed).
 */
export function splitMarkerGroup(group: string): string[] {
  return group.split(",").map((m) => m.trim()).filter(Boolean);
}
