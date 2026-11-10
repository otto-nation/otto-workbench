/**
 * Review bodies as HTML for the page. Review text quotes PR content, which anyone who can open a PR
 * controls, so the whole input is escaped before any rule runs: nothing a rule emits can carry markup
 * from the input. Links are kept only for http(s) URLs. Pure.
 *
 * Subset: # headings (rendered h3–h6), - / * / 1. lists, ``` fences, `code`, **bold**, [text](url),
 * > quotes, --- rules, paragraphs. Tables are shown as preformatted text.
 */
// ceiling: a hand-rolled subset; vendor a sanitising renderer if review bodies start using
// markdown this does not draw (nested lists, images)

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
}

function inline(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" rel="noopener noreferrer" target="_blank">$1</a>');
  return s;
}

export function renderMarkdown(text) {
  const lines = String(text ?? '').replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  let para = [];
  let list = null;
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join('')}</${list.tag}>`); list = null; } };
  const flush = () => { flushPara(); flushList(); };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      flush();
      const body = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      out.push(`<pre><code>${escapeHtml(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (/^\s*\|/.test(line)) {
      flush();
      const rows = [];
      for (; i < lines.length && /^\s*\|/.test(lines[i]); i++) rows.push(lines[i]);
      i--;
      out.push(`<pre class="table">${escapeHtml(rows.join('\n'))}</pre>`);
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) { flush(); const n = Math.min(6, heading[1].length + 2); out.push(`<h${n}>${inline(heading[2])}</h${n}>`); continue; }
    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { flush(); out.push('<hr>'); continue; }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushPara();
      const tag = bullet ? 'ul' : 'ol';
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [] };
      list.items.push((bullet ?? numbered)[1]);
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) { flush(); out.push(`<blockquote>${inline(quote[1])}</blockquote>`); continue; }
    if (line.trim() === '') { flush(); continue; }
    flushList();
    para.push(line.trim());
  }
  flush();
  return out.join('\n');
}
