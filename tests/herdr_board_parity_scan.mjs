#!/usr/bin/env node
// Board-parity scanner: recursive *.js under roots; comments, strings,
// template-literal contents and regex literals stripped. Import specifiers
// kept so re-exports and dynamic import() of banned files still hit.
// See tests/herdr_board_parity.bats.
import fs from "node:fs";
import path from "node:path";

const [re, ...given] = process.argv.slice(2);
if (!re) {
  process.stderr.write("usage: herdr_board_parity_scan.mjs <identifier-regex> [root...]\n");
  process.exit(2);
}
const word = new RegExp("\\b(" + re + ")\\b", "g");
const fromEnv = (process.env.PARITY_ROOTS || "").split("\n").filter(Boolean);
const defaultRoots = [
  process.env.WEB,
  process.env.MODS ? path.join(process.env.MODS, "prs") : "",
].filter(Boolean);
const targets = given.length ? given : (fromEnv.length ? fromEnv : defaultRoots);
if (!targets.length) {
  process.stderr.write("herdr_board_parity_scan.mjs: no roots (pass argv, PARITY_ROOTS, or WEB/MODS)\n");
  process.exit(2);
}

function walkJs(root) {
  const st = fs.statSync(root);
  if (st.isFile()) return root.endsWith(".js") ? [root] : [];
  const out = [];
  const ents = fs.readdirSync(root, { withFileTypes: true })
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const ent of ents) {
    const p = path.join(root, ent.name);
    if (ent.isDirectory()) out.push(...walkJs(p));
    else if (ent.isFile() && ent.name.endsWith(".js")) out.push(p);
  }
  return out;
}

function scan(src, i, stopBrace) {
  let out = "";
  let prev = "op";
  let depth = 0;
  const n = src.length;
  while (i < n) {
    const c = src[i];
    const c1 = i + 1 < n ? src[i + 1] : "";
    if (c === " " || c === "\t" || c === "\n" || c === "\r") {
      out += c;
      i++;
      continue;
    }
    if (stopBrace && c === "}" && depth === 0) return { out, i: i + 1 };
    if (c === "/" && c1 === "/") {
      while (i < n && src[i] !== "\n") i++;
      continue;
    }
    if (c === "/" && c1 === "*") {
      i += 2;
      while (i + 1 < n && !(src[i] === "*" && src[i + 1] === "/")) i++;
      i += 2;
      out += " ";
      continue;
    }
    if (c === "'" || c === '"') {
      i++;
      while (i < n) {
        if (src[i] === "\\") { i += 2; continue; }
        if (src[i] === c) { i++; break; }
        if (src[i] === "\n") break;
        i++;
      }
      out += "''";
      prev = "id";
      continue;
    }
    if (c === "`") {
      i++;
      while (i < n && src[i] !== "`") {
        if (src[i] === "\\") { i += 2; continue; }
        if (src[i] === "$" && i + 1 < n && src[i + 1] === "{") {
          const inner = scan(src, i + 2, true);
          out += " " + inner.out + " ";
          i = inner.i;
          continue;
        }
        i++;
      }
      if (i < n && src[i] === "`") i++;
      out += "''";
      prev = "id";
      continue;
    }
    if (c === "/" && prev === "op") {
      i++;
      while (i < n) {
        if (src[i] === "\\") { i += 2; continue; }
        if (src[i] === "[") {
          i++;
          while (i < n && src[i] !== "]") {
            if (src[i] === "\\") i += 2;
            else i++;
          }
          i++;
          continue;
        }
        if (src[i] === "/" || src[i] === "\n") {
          if (src[i] === "/") i++;
          break;
        }
        i++;
      }
      while (i < n && /[a-z]/i.test(src[i])) i++;
      out += "''";
      prev = "id";
      continue;
    }
    if (/[A-Za-z_$]/.test(c)) {
      let j = i + 1;
      while (j < n && /[A-Za-z0-9_$]/.test(src[j])) j++;
      out += src.slice(i, j);
      i = j;
      prev = "id";
      continue;
    }
    if (/[0-9]/.test(c)) {
      out += c;
      i++;
      prev = "id";
      continue;
    }
    if (c === "{") depth++;
    if (c === "}") depth--;
    out += c;
    i++;
    prev = (c === ")" || c === "]" || c === "}") ? "id" : "op";
  }
  return { out, i };
}

function strip(src) {
  return scan(src, 0, false).out;
}

function specBase(spec) {
  return spec.split("/").pop().replace(/\.js$/, "");
}

function specHits(src, wordRe) {
  const hits = [];
  const consider = (spec, named) => {
    const base = specBase(spec);
    if (named && base === "board_data") return;
    wordRe.lastIndex = 0;
    const m = wordRe.exec(base);
    if (m) hits.push(m[1]);
  };
  const fromRe = /\b(?:import|export)\s+([\s\S]*?)\sfrom\s*(['"])([^'"]+)\2/g;
  let m;
  while ((m = fromRe.exec(src))) {
    consider(m[3], m[1].replace(/\s+/g, " ").trim().startsWith("{"));
  }
  const dynRe = /\bimport\s*\(\s*(['"])([^'"]+)\1/g;
  while ((m = dynRe.exec(src))) consider(m[2], false);
  const sideRe = /\bimport\s*(['"])([^'"]+)\1/g;
  while ((m = sideRe.exec(src))) consider(m[2], false);
  return hits;
}

const files = targets.flatMap(walkJs).sort();
const hits = [];
const root = process.env.REPO_ROOT ? process.env.REPO_ROOT + "/" : "";
for (const f of files) {
  const src = fs.readFileSync(f, "utf8");
  const rel = f.startsWith(root) ? f.slice(root.length) : f;
  for (const id of specHits(src, word)) hits.push(rel + ":" + id);
  word.lastIndex = 0;
  for (const m of strip(src).matchAll(word)) hits.push(rel + ":" + m[1]);
}
process.stdout.write([...new Set(hits)].join("\n"));
