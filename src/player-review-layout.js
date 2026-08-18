export function createPlayerReviewLayout({
  document,
  getOverlay,
  getTimelineOverlay,
  localStorage,
  setStatus,
  storageKey,
  window,
}) {
  let drag = null;
  let zIndex = 1200;
  let positions = {};
  try {
    const stored = JSON.parse(localStorage?.getItem(storageKey) || '{}');
    if (stored?.version === 1 && stored.positions && typeof stored.positions === 'object') {
      positions = stored.positions;
    }
  } catch (_) { /* Invalid or unavailable storage uses educated defaults. */ }

  function finite(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function viewport() {
    return {
      width: Math.max(320, Number(window.innerWidth) || document.documentElement?.clientWidth || 1280),
      height: Math.max(240, Number(window.innerHeight) || document.documentElement?.clientHeight || 720),
    };
  }

  function size(node, kind) {
    const rect = node?.getBoundingClientRect?.() || {};
    return {
      width: Math.max(1, finite(rect.width) || finite(node?.offsetWidth) || (kind === 'review' ? 464 : 640)),
      height: Math.max(1, finite(rect.height) || finite(node?.offsetHeight) || (kind === 'review' ? 640 : 190)),
    };
  }

  function defaultPosition(kind, node) {
    const bounds = viewport();
    const dimensions = size(node, kind);
    if (kind === 'review') {
      return { x: Math.max(8, bounds.width - dimensions.width - 16), y: 16 };
    }
    const reviewLeft = bounds.width - Math.min(464, Math.max(0, bounds.width - 32)) - 16;
    return {
      x: Math.max(8, Math.min((bounds.width - dimensions.width) / 2, reviewLeft - dimensions.width - 16)),
      y: 92,
    };
  }

  function clamp(kind, node, position) {
    const bounds = viewport();
    const dimensions = size(node, kind);
    return {
      x: Math.max(8, Math.min(
        bounds.width - Math.min(dimensions.width, bounds.width - 16) - 8,
        finite(position?.x) ?? 8,
      )),
      y: Math.max(8, Math.min(bounds.height - 48, finite(position?.y) ?? 8)),
    };
  }

  function save() {
    try {
      localStorage?.setItem(storageKey, JSON.stringify({ version: 1, positions }));
    } catch (_) { /* Persistence is best effort. */ }
  }

  function move(kind, node, x, y, { persist = true } = {}) {
    const position = clamp(kind, node, { x, y });
    node.style.left = `${position.x}px`;
    node.style.top = `${position.y}px`;
    node.style.right = 'auto';
    node.style.bottom = 'auto';
    positions[kind] = position;
    if (persist) save();
  }

  function position(kind, node, { useDefault = false } = {}) {
    if (!node) return;
    const requested = !useDefault && positions[kind]
      ? positions[kind]
      : defaultPosition(kind, node);
    const next = clamp(kind, node, requested);
    move(kind, node, next.x, next.y, { persist: false });
  }

  function bringForward(node) {
    zIndex += 1;
    node.style.zIndex = String(zIndex);
  }

  function finishDrag() {
    if (!drag) return;
    drag = null;
    document.body.classList.remove('lh-player-review-dragging');
    window.removeEventListener('pointermove', continueDrag);
    window.removeEventListener('pointerup', finishDrag);
    window.removeEventListener('pointercancel', finishDrag);
    save();
  }

  function continueDrag(event) {
    if (!drag) return;
    move(
      drag.kind,
      drag.node,
      drag.startX + Number(event.clientX || 0) - drag.pointerX,
      drag.startY + Number(event.clientY || 0) - drag.pointerY,
      { persist: false },
    );
    event.preventDefault?.();
  }

  function beginDrag(event, kind, node) {
    if ((event.button ?? 0) !== 0) return;
    bringForward(node);
    const current = positions[kind] || defaultPosition(kind, node);
    drag = {
      kind,
      node,
      pointerX: Number(event.clientX || 0),
      pointerY: Number(event.clientY || 0),
      startX: finite(current.x) ?? 0,
      startY: finite(current.y) ?? 0,
    };
    document.body.classList.add('lh-player-review-dragging');
    window.addEventListener('pointermove', continueDrag);
    window.addEventListener('pointerup', finishDrag);
    window.addEventListener('pointercancel', finishDrag);
    event.currentTarget?.setPointerCapture?.(event.pointerId);
    event.preventDefault?.();
  }

  function installHandle(handle, kind, node) {
    handle.classList.add('lh-player-review-drag-handle');
    handle.tabIndex = 0;
    handle.setAttribute('role', 'button');
    handle.setAttribute('aria-label', `Move Library Doctor ${kind === 'review' ? 'review' : 'timeline'} window`);
    handle.addEventListener('pointerdown', (event) => beginDrag(event, kind, node));
    handle.addEventListener('keydown', (event) => {
      const deltas = {
        ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
      };
      const delta = deltas[event.key];
      if (!delta) return;
      const step = event.shiftKey ? 24 : 8;
      const current = positions[kind] || defaultPosition(kind, node);
      move(kind, node, current.x + delta[0] * step, current.y + delta[1] * step);
      event.preventDefault();
    });
    node.addEventListener('pointerdown', () => bringForward(node));
  }

  function reflow() {
    position('review', getOverlay());
    position('timeline', getTimelineOverlay());
    save();
  }

  function reset() {
    positions = {};
    try { localStorage?.removeItem(storageKey); } catch (_) { /* best effort */ }
    position('review', getOverlay(), { useDefault: true });
    position('timeline', getTimelineOverlay(), { useDefault: true });
    save();
    setStatus('Player Review windows returned to their default positions.', 'good');
  }

  window.addEventListener('resize', reflow);
  return {
    destroy() {
      finishDrag();
      window.removeEventListener('resize', reflow);
    },
    installHandle,
    position,
    reflow,
    reset,
  };
}
