#!/usr/bin/env bats
# Tests for ai/herdr/web/lib/markdown.js — escape-first review-body subset (spec § 7.6).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

@test "markdown: headings, lists, code, bold, fenced code, tables as pre" {
  _web "const { renderMarkdown } = await import(W + '/lib/markdown.js');
        out(renderMarkdown('# Must fix\n\n- **a** in \`x.js:4\`\n- b\n\n1. one\n\n\`\`\`\nconst a = 1 < 2;\n\`\`\`\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\npara\nline'));"
  [ "$status" -eq 0 ]
  [ "$output" = '"<h3>Must fix</h3>\n<ul><li><strong>a</strong> in <code>x.js:4</code></li><li>b</li></ul>\n<ol><li>one</li></ol>\n<pre><code>const a = 1 &lt; 2;</code></pre>\n<pre class=\"table\">| a | b |\n|---|---|\n| 1 | 2 |</pre>\n<p>para line</p>"' ]
}

@test "markdown: markup in the input stays text; only http(s) links become anchors" {
  _web "const { renderMarkdown } = await import(W + '/lib/markdown.js');
        const h = renderMarkdown('<script>alert(1)</script> <img src=x onerror=alert(1)> [a](javascript:alert(1)) [b](https://x.test/?q=\"1\") \`<b>\`');
        out({ script: h.includes('<script'), img: h.includes('<img'), js: h.includes('href=\"javascript'),
              link: h.includes('<a href=\"https://x.test/?q=&quot;1&quot;\" rel=\"noopener noreferrer\" target=\"_blank\">b</a>'),
              code: h.includes('<code>&lt;b&gt;</code>') });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"script":false,"img":false,"js":false,"link":true,"code":true}' ]
}
