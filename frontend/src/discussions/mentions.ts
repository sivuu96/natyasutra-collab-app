// Pure helpers for "@" suggestions in the message composer. No React here, so they are easy to test.

export interface MentionCandidate {
  handle: string;
  displayName: string;
  kind: "agent" | "person";
}

export interface ActiveMention {
  /** Index of the "@" that starts the token. */
  start: number;
  /** Text typed after the "@", without the "@". */
  query: string;
}

const HANDLE_CHAR = /[a-z0-9_-]/i;

/** The @-token the caret is inside, or null. Only a "@" at the start of a word counts. */
export function activeMention(text: string, caret: number): ActiveMention | null {
  let index = caret - 1;
  while (index >= 0 && HANDLE_CHAR.test(text[index])) index -= 1;
  if (index < 0 || text[index] !== "@") return null;
  if (index > 0 && !/\s/.test(text[index - 1])) return null; // an email address, not a mention
  return { start: index, query: text.slice(index + 1, caret) };
}

/** Agents before people, so a mention of an agent is the first choice. */
function kindRank(candidate: MentionCandidate): number {
  return candidate.kind === "agent" ? 0 : 1;
}

/** Candidates whose handle starts with the query, case-insensitive, best matches first. */
export function suggestMentions(candidates: MentionCandidate[], query: string, limit = 6): MentionCandidate[] {
  const q = query.toLowerCase();
  return candidates
    .filter((c) => c.handle.toLowerCase().startsWith(q))
    .sort((a, b) => kindRank(a) - kindRank(b) || a.handle.localeCompare(b.handle))
    .slice(0, limit);
}

/** Replace the @-token with "@handle " and return the new text and caret position. */
export function applyMention(
  text: string,
  mention: ActiveMention,
  caret: number,
  handle: string,
): { text: string; caret: number } {
  const after = text.slice(caret);
  // Add a separating space only when the text after the caret does not already start with one.
  const insert = `@${handle}${/^\s/.test(after) ? "" : " "}`;
  const next = text.slice(0, mention.start) + insert + after;
  return { text: next, caret: mention.start + insert.length };
}
