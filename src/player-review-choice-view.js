export function playerReviewReasonCopy(reason) {
  return {
    both_flags: 'Both hammer-on and pull-off are stored on this note.',
    direction_mismatch: 'The stored direction conflicts with the incoming fret movement.',
    same_fret: 'A same-fret transition carries a hammer-on or pull-off flag.',
    no_usable_predecessor: 'No single usable previous note explains this HO/PO flag.',
  }[reason] || reason;
}

export function playerReviewHighlightCopy(candidate, status, number) {
  const location = `string ${number(Number(candidate.string) + 1)}, fret ${number(candidate.fret)}`;
  return status?.installed && status?.target
    ? `Pulsing note = current issue · ${location}. Bundled 2D and 3D Highway views show the pulse; other renderers may use this text only.`
    : `Current issue · ${location} at ${Number(candidate.time).toFixed(4)}s. This renderer is not currently showing Library Doctor's pulse; use Jump to issue and this location.`;
}

export function playerReviewSummaryCopy({ accepted, approval = {}, skipped, total, number }) {
  const unresolved = Math.max(0, total - accepted - skipped);
  const parts = [
    `${number(accepted)} accepted change${accepted === 1 ? '' : 's'}`,
    `${number(skipped)} skipped for now`,
    `${number(unresolved)} unresolved`,
  ];
  if (approval.pending) {
    parts.push(`Rechecking ${number(approval.pending)} preserved choice${approval.pending === 1 ? '' : 's'}`);
  }
  if (approval.failed) {
    parts.push(`${number(approval.failed)} preserved choice check${approval.failed === 1 ? '' : 's'} failed — Retry or Skip`);
  }
  return parts.join(' · ');
}

export function playerReviewChoiceNodes({
  candidate,
  decisionDefinitions,
  document,
  disabled,
  make,
  onRetry,
  onSelect,
  optionState,
  selected,
}) {
  if (!optionState || optionState.status === 'loading') {
    return [make(
      'p',
      'lh-player-review-note',
      'Checking every possible choice against a temporary copy of this arrangement…',
    )];
  }
  if (optionState.status === 'error') {
    const panel = make('div', 'lh-inline-error');
    panel.appendChild(make(
      'p',
      '',
      optionState.error?.message || 'Library Doctor could not evaluate this issue.',
    ));
    const retry = make('button', 'lh-button', 'Retry choice check');
    retry.type = 'button';
    retry.addEventListener('click', onRetry);
    panel.appendChild(retry);
    return [panel];
  }
  const response = optionState.response;
  if (!(response?.decision_names || []).length) {
    return [make(
      'p',
      'lh-repair-warning',
      response?.message
        || 'No registered HO/PO change resolves this issue without creating another finding. Skip it for now and edit the tab manually.',
    )];
  }
  const fieldset = make('fieldset', 'lh-player-review-decisions');
  fieldset.appendChild(make('legend', '', 'Preview a choice that resolves this issue'));
  response.decision_names.forEach((name) => {
    const definition = decisionDefinitions.get(name);
    if (!definition) return;
    const label = make('label', 'lh-player-review-choice');
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = `player-review-${candidate.review_item_id}`;
    radio.value = name;
    radio.checked = selected === name;
    radio.disabled = disabled;
    radio.addEventListener('change', () => onSelect(name, definition));
    const copy = make('span');
    copy.appendChild(make('strong', '', definition.label));
    copy.appendChild(make('small', '', definition.description));
    label.appendChild(radio);
    label.appendChild(copy);
    fieldset.appendChild(label);
  });
  return [fieldset];
}
