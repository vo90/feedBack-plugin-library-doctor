export function createPlayerReviewOverlay({
  document,
  getTimeline,
  layout,
  make,
  onReturn,
  text,
  window,
}) {
  let overlay = null;
  let body = null;
  let status = null;

  function ensure() {
    if (overlay?.isConnected) return overlay;
    overlay = make('aside', 'lh-player-review-overlay');
    overlay.id = 'lh-player-review-overlay';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'false');
    overlay.setAttribute('aria-label', 'Library Doctor Player Review');
    const header = make('header', 'lh-player-review-overlay-header');
    const title = make('div', 'lh-player-review-overlay-title');
    title.appendChild(make('span', 'lh-player-review-kicker', 'Library Doctor'));
    title.appendChild(make('strong', '', 'Player Review'));
    layout.installHandle(title, 'review', overlay);
    const actions = make('div', 'lh-player-review-overlay-actions');
    const reset = make('button', 'lh-button lh-player-review-reset-layout', 'Reset layout');
    reset.type = 'button';
    reset.addEventListener('click', layout.reset);
    const returnButton = make('button', 'lh-button', 'Return to Library Doctor');
    returnButton.type = 'button';
    returnButton.addEventListener('click', onReturn);
    actions.appendChild(reset);
    actions.appendChild(returnButton);
    header.appendChild(title);
    header.appendChild(actions);
    status = make('p', 'lh-player-review-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    body = make('div', 'lh-player-review-body');
    overlay.appendChild(header);
    overlay.appendChild(status);
    overlay.appendChild(body);
    document.body.appendChild(overlay);
    layout.position('review', overlay);
    return overlay;
  }

  function appendRecovery(container, session, onResolve) {
    if (!session?.pendingRecovery) return;
    const panel = make('section', 'lh-player-review-recovery');
    panel.appendChild(make('strong', '', 'One repair checkpoint is waiting'));
    panel.appendChild(make(
      'p',
      '',
      'You may keep reviewing and accepting choices. Undo or finalize this checkpoint before applying another group.',
    ));
    const controls = make('div', 'lh-player-review-buttons');
    const undo = make('button', 'lh-button', 'Undo applied group');
    const finalize = make('button', 'lh-button', 'Finalize applied group');
    undo.type = finalize.type = 'button';
    undo.disabled = finalize.disabled = session.busy;
    undo.addEventListener('click', () => onResolve('restore'));
    finalize.addEventListener('click', () => onResolve('finalize'));
    controls.appendChild(undo);
    controls.appendChild(finalize);
    panel.appendChild(controls);
    container.appendChild(panel);
  }

  return {
    appendRecovery,
    destroy() {
      overlay?.remove();
      overlay = null;
      body = null;
      status = null;
    },
    ensure,
    getBody() {
      ensure();
      return body;
    },
    getOverlay: () => overlay,
    hide() {
      if (overlay) overlay.hidden = true;
      getTimeline()?.hide();
    },
    prepare() {
      ensure().hidden = false;
    },
    setStatus(value, tone = '') {
      ensure();
      text(status, value);
      status.dataset.tone = tone;
    },
    show() {
      ensure().hidden = false;
      getTimeline()?.show();
      window.requestAnimationFrame?.(() => layout.reflow());
    },
  };
}
