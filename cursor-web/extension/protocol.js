/* Protocol JSON must be read from a literal code block, never rendered prose.
 * No backslash replacement, unescaping, Markdown-to-JSON or fragment search.
 */
const ZSWebProtocol = (() => {
  function read(snapshot) {
    const blocks = new Set();
    for (const root of snapshot.replyRoots || []) {
      if (!root) continue;
      // Providers scope these roots to the selected final answer, not reasoning.
      for (const node of root.querySelectorAll('pre, .cm-content')) {
        // CodeMirror commonly lives inside pre. Treat that as ONE block.
        if (node.matches('.cm-content') && node.closest('pre')) continue;
        blocks.add(node);
      }
    }
    if (blocks.size !== 1) {
      return {text:'', source:'code_block_unavailable', error:
        `Protocol response requires exactly one JSON code block; found ${blocks.size}. Rendered prose was not used because Markdown can alter backslashes. Reload the updated extension and request a fenced json response.`};
    }
    const block = [...blocks][0];
    const cm = block.matches('.cm-content') ? block : block.querySelector('.cm-content');
    if (cm) {
      document.dispatchEvent(new CustomEvent('zs-cm-sync'));
      const raw = cm.getAttribute('data-zs-cm');
      if (raw === null) return {text:'', source:'codemirror_unavailable', error:
        'Full CodeMirror source is unavailable. Refresh the webpage with the updated extension; visible lines may be truncated and are not used.'};
      return {text:raw, source:'codemirror_document'};
    }
    const code = block.querySelector('code');
    if (code) return {text:code.textContent || '', source:'code_text'};
    // A pre element may contain syntax highlighting spans without a code tag.
    // Remove only UI buttons from a detached clone, not whitespace or escapes.
    const copy = block.cloneNode(true);
    for (const button of copy.querySelectorAll('button, [role="button"]')) button.remove();
    return {text:copy.textContent || '', source:'pre_text'};
  }
  return {read};
})();
