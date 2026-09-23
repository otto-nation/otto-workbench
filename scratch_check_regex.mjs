const tests = ["env sudo -s","time sudo -i","nohup sudo -s","nice sudo -i","xargs sudo -s","sudo -s"];
const re = /^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*(?:sudo|doas)\b/;
for (const t of tests) console.log(t, "=>", re.test(t));
