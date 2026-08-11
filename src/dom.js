export function createDomPrimitives(document) {
  function setHidden(node, hidden) {
    if (node) node.hidden = !!hidden;
  }

  function text(node, value) {
    if (node) node.textContent = value == null ? '' : String(value);
  }

  function number(value) {
    return Number(value || 0).toLocaleString();
  }

  function make(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = String(value);
    return node;
  }

  function badge(value, tone) {
    return make('span', `lh-badge${tone ? ` lh-badge-${tone}` : ''}`, value);
  }

  function focus(node) {
    if (!node || typeof node.focus !== 'function') return;
    queueMicrotask(() => {
      if (node.isConnected && !node.disabled && !node.hidden) node.focus();
    });
  }

  function createConfirmation({
    className,
    message,
    confirmLabel,
    confirmClass = 'lh-button lh-button-primary',
    cancelLabel = 'Cancel',
    trigger,
    onConfirm,
    onCancel,
  }) {
    const region = make('div', className);
    region.setAttribute('role', 'group');
    region.setAttribute('aria-label', 'Confirmation');
    if (message) region.appendChild(make('p', '', message));
    const confirm = make('button', confirmClass, confirmLabel);
    const cancel = make('button', 'lh-button', cancelLabel);
    confirm.type = 'button';
    cancel.type = 'button';
    confirm.addEventListener('click', () => onConfirm(confirm, cancel, region));
    cancel.addEventListener('click', () => {
      region.remove();
      if (trigger) trigger.disabled = false;
      if (onCancel) onCancel();
      focus(trigger);
    });
    region.appendChild(confirm);
    region.appendChild(cancel);
    focus(confirm);
    return { region, confirm, cancel };
  }

  return { setHidden, text, number, make, badge, focus, createConfirmation };
}
