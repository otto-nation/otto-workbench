/** index.html / login.html templating: `{{key}}` slots, every value HTML-escaped. */
import { escapeHtml } from '../lib/markdown.js';

export function fill(template, values) {
  return template.replace(/\{\{(\w+)\}\}/g, (_, key) => {
    if (!(key in values)) throw new Error(`template slot {{${key}}} has no value`);
    return escapeHtml(values[key]);
  });
}
