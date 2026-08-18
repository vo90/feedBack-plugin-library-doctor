function initialState() {
  return {
    active: false,
    activationGeneration: 0,
    filter: 'problems',
    ruleCode: '',
    query: '',
    offset: 0,
    status: null,
    results: null,
    pollTimer: 0,
    searchTimer: 0,
    resultRequest: 0,
    ruleMetadata: {},
    repairRules: {},
    reviewedRepairAdapters: {},
    reviewedRuleAdapters: {},
    allSafeRepair: null,
    batch: null,
    batchRenderKey: '',
    batchAttentionKey: '',
    latestRepair: null,
    dismissedRepairId: null,
    dashboardView: '',
    legacyLayout: false,
    targetKind: 'library',
    targetPaths: { folder: '', file: '' },
    workerMode: 'automatic',
    workerLimit: 1,
    reviewDifficultyDefaultScope: 'full_only',
    reviewDifficultyScope: 'full_only',
    workspace: 'health',
    songTools: {
      query: '',
      page: 0,
      total: 0,
      items: [],
      requestId: 0,
      loaded: false,
      selected: null,
      activeTool: '',
      selectionRequest: 0,
      searchTimer: 0,
    },
  };
}

export function createLibraryDoctorStore({ AbortController: AbortControllerImpl = globalThis.AbortController } = {}) {
  const state = initialState();
  let controller = null;

  function activate() {
    if (controller) controller.abort();
    controller = new AbortControllerImpl();
    state.activationGeneration += 1;
    state.active = true;
    return capture();
  }

  function deactivate() {
    state.active = false;
    state.activationGeneration += 1;
    if (controller) controller.abort();
    controller = null;
  }

  function capture() {
    return {
      generation: state.activationGeneration,
      signal: controller?.signal || null,
    };
  }

  function isCurrent(activation) {
    return !!activation
      && state.active
      && activation.generation === state.activationGeneration
      && !activation.signal?.aborted;
  }

  return { state, activate, deactivate, capture, isCurrent };
}
