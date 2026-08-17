/**
 * Drop the document's leading H1.
 *
 * Every `docs/*.md` opens with an H1 because GitHub needs one, and fumadocs renders
 * frontmatter `title` as the page heading. Without this the title appears twice.
 */
export function remarkStripTitle() {
  return (tree) => {
    const first = tree.children.find((node) => node.type === 'heading');
    if (first?.depth === 1) {
      tree.children.splice(tree.children.indexOf(first), 1);
    }
  };
}
