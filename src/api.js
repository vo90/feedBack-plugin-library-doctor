import { API_ROOT } from './constants.js';

export function isAbortError(error) {
  return error?.name === 'AbortError' || error?.code === 'ABORT_ERR';
}

export function createApiClient({ fetch: fetchImpl, activation, apiRoot = API_ROOT }) {
  if (typeof fetchImpl !== 'function') throw new TypeError('A fetch implementation is required.');

  async function requestUrl(url, options = {}, { activationBound = true } = {}) {
    const activeRequest = activationBound ? activation.capture() : null;
    const requestOptions = { ...options };
    if (activationBound && activeRequest?.signal && !requestOptions.signal) {
      requestOptions.signal = activeRequest.signal;
    }

    const response = await fetchImpl(url, requestOptions);
    if (activationBound && !activation.isCurrent(activeRequest)) {
      throw new DOMException('Library Doctor activation changed.', 'AbortError');
    }

    let body = null;
    try { body = await response.json(); } catch (_) { /* normalized below */ }
    if (!response.ok) {
      const detail = body && (body.detail || body.error);
      const message = typeof detail === 'string'
        ? detail
        : detail && typeof detail.message === 'string'
          ? detail.message
          : `Request failed (${response.status})`;
      const error = new Error(message);
      error.code = detail && typeof detail === 'object' ? detail.code : null;
      error.fileState = detail && typeof detail === 'object' ? detail.file_state : null;
      error.retryable = Boolean(
        detail && typeof detail === 'object' && detail.retryable,
      );
      error.nextAction = detail && typeof detail === 'object'
        ? detail.next_action || null
        : null;
      error.status = response.status;
      throw error;
    }
    return body;
  }

  return {
    request(path, options) {
      return requestUrl(apiRoot + path, options);
    },
    requestGlobal(path, options) {
      return requestUrl(apiRoot + path, options, { activationBound: false });
    },
    coreRequest(path, options) {
      return requestUrl(path, options);
    },
  };
}
