export function parseSavedMarkdown(
  text: string
): { title: string; summary: string; body_markdown: string } {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/);
  if (!m) {
    return {
      title: "Article",
      summary: "",
      body_markdown: text.trim(),
    };
  }
  const block = m[1];
  const body_markdown = text.slice(m[0].length).trim();
  let title = "Article";
  let summary = "";
  for (const line of block.split("\n")) {
    const t = line.match(/^title:\s*(.*)$/);
    if (t) {
      title = unquoteYamlString(t[1].trim());
    }
    const s = line.match(/^summary:\s*(.*)$/);
    if (s) {
      summary = unquoteYamlString(s[1].trim());
    }
  }
  return { title, summary, body_markdown };
}

function unquoteYamlString(raw: string): string {
  if (raw.length >= 2 && raw[0] === '"' && raw[raw.length - 1] === '"') {
    return raw.slice(1, -1).replace(/\\"/g, '"');
  }
  return raw;
}
