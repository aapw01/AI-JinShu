/**
 * Parse raw chapter content into display-ready paragraphs.
 *
 * Handles multiple content formats that may come from different LLM outputs:
 * - Pure text with \n or \n\n paragraph breaks
 * - Escaped newlines (literal \\n in JSON-decoded strings)
 * - Light HTML tags (<p>, <br>, <div>)
 * - Markdown artifacts (code fences, headings)
 * - <chapter_body> wrapper tags
 */

export interface DisplayParagraph {
  type: "text" | "break";
  content: string;
}

const HTML_BLOCK_TAG_RE = /<\/?(?:p|div|br)\s*\/?>/gi;
const CHAPTER_BODY_TAG_RE = /<chapter_body>([\s\S]*?)<\/chapter_body>/i;
const CODE_FENCE_RE = /^```[\s\S]*?```$/gm;
const MD_HEADING_RE = /^#{1,6}\s+/gm;
const MD_BOLD_RE = /\*\*(.*?)\*\*/g;

function stripWrappers(raw: string): string {
  let text = raw;

  const bodyMatch = CHAPTER_BODY_TAG_RE.exec(text);
  if (bodyMatch) {
    text = bodyMatch[1] || "";
  }

  if (text.startsWith("```")) {
    const lines = text.split("\n");
    const trimmed = lines.slice(1);
    if (trimmed.length > 0 && trimmed[trimmed.length - 1].trim() === "```") {
      trimmed.pop();
    }
    text = trimmed.join("\n");
  }

  text = text.replace(CODE_FENCE_RE, "");

  return text;
}

function normalizeLineBreaks(text: string): string {
  let result = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  result = result.replace(/(?<!\\)\\n/g, "\n");

  result = result.replace(HTML_BLOCK_TAG_RE, "\n");

  result = result.replace(MD_HEADING_RE, "");
  result = result.replace(MD_BOLD_RE, "$1");

  return result;
}

/**
 * Parse chapter content string into an array of display paragraphs.
 * Empty entries between paragraphs become `{ type: "break" }` elements
 * for visual spacing.
 */
export function parseChapterContent(raw: string | null | undefined): DisplayParagraph[] {
  if (!raw || !raw.trim()) {
    return [];
  }

  let text = stripWrappers(raw);
  text = normalizeLineBreaks(text);

  const blocks = text.split(/\n\s*\n/);
  const result: DisplayParagraph[] = [];

  for (const block of blocks) {
    const trimmed = block.trim();
    if (!trimmed) continue;

    const innerLines = trimmed
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

    for (const line of innerLines) {
      result.push({ type: "text", content: line });
    }
  }

  return result;
}
