/* Protocol JSON must be read from a literal code block, never rendered prose.
 * No backslash replacement, unescaping, Markdown-to-JSON or fragment search.
 *
 * Scope order:
 *  1. The provider's non-reasoning answer roots (replyRoots).
 *  2. Only if those contain ZERO blocks, the whole answer turn (item), with
 *     the provider's reasoning area (thinkingSel) and ZeroScript UI excluded.
 * A container and its inner pre/code count once (outermost wins). A failed
 * search reports which scope was used and how many blocks existed where, so
 * the next failure is diagnosable from the endpoint error alone.
 */
const ZSWebProtocol = (() => {
  const BLOCK_SEL = 'pre, .cm-content, .md-code-block';
  const UI_SEL = '#zs-root, .zs-chip';

  function outermost(scope, excludeSel) {
    const found = [];
    for (const el of scope) {
      if (!el) continue;
      for (const node of el.querySelectorAll(BLOCK_SEL)) {
        if (excludeSel && node.closest(excludeSel)) continue;
        if (node.closest(UI_SEL)) continue;
        found.push(node);
      }
    }
    return found.filter((n) => !found.some((o) => o !== n && o.contains(n)));
  }

  function read(snapshot) {
    const roots = (snapshot.replyRoots || []).filter(Boolean);
    const item = snapshot.item || null;
    const thinkingSel = snapshot.thinkingSel || null;

    let blocks = outermost(roots, null);
    let scope = 'answer_roots';
    if (blocks.length === 0 && item) {
      blocks = outermost([item], thinkingSel);
      scope = 'answer_turn';
    }

    let thinkingBlocks = 0;
    if (item && thinkingSel) {
      thinkingBlocks = outermost([item], null).filter((n) => n.closest(thinkingSel)).length;
    }
    const detail =
      `roots=${roots.length} scope=${scope} turn_blocks=${blocks.length}` +
      (thinkingSel ? ` thinking_blocks=${thinkingBlocks}` : '');

    if (blocks.length !== 1) {
      return {text: '', source: 'code_block_unavailable', detail, error:
        `Protocol response requires exactly one JSON code block; found ${blocks.length} in ${scope} (${detail}). Rendered prose was not used because Markdown can alter backslashes. Reload the updated extension and request a fenced json response.`};
    }

    const block = blocks[0];
    const cm = block.matches('.cm-content') ? block : block.querySelector('.cm-content');
    if (cm) {
      document.dispatchEvent(new CustomEvent('zs-cm-sync'));
      const raw = cm.getAttribute('data-zs-cm');
      if (raw === null) return {text: '', source: 'codemirror_unavailable', detail, error:
        'Full CodeMirror source is unavailable. Refresh the webpage with the updated extension; visible lines may be truncated and are not used.'};
      return {text: raw, source: 'codemirror_document', detail};
    }
    const code = block.querySelector('code');
    if (code) return {text: code.textContent || '', source: 'code_text', detail};
    // A pre element may contain syntax highlighting spans without a code tag.
    // Remove only UI chrome from a detached clone, never whitespace or escapes.
    const copy = block.cloneNode(true);
    for (const ui of copy.querySelectorAll('button, [role="button"], .md-code-block-banner')) ui.remove();
    return {text: copy.textContent || '', source: 'pre_text', detail};
  }

  return {read};
})();
