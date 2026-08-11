export function createSongToolsController({
  actions: actionRegistry,
  apiRoot,
  badge,
  coreRequest,
  document,
  focus,
  getElements,
  make,
  number,
  request,
  setHidden,
  songToolPageSize,
  state,
  text,
}) {
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });

  function currentPreviewUrl(packageName) {
    return `${apiRoot}/repair/media/current?package=${encodeURIComponent(packageName)}&v=${Date.now()}`;
  }

  function setWorkspace(workspace) {
    const next = workspace === 'tools' ? 'tools' : 'health';
    state.workspace = next;
    setHidden(el.healthWorkspace, next !== 'health');
    setHidden(el.songToolsWorkspace, next !== 'tools');
    el.workspaceTabs.querySelectorAll('[data-workspace]').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.workspace === next));
    });
    if (next === 'tools') {
      loadSongTools();
    }
  }

  function songPackage(song) {
    return typeof song?.filename === 'string'
      ? song.filename.replaceAll('\\', '/')
      : '';
  }

  function renderSongToolLibrary() {
    const tools = state.songTools;
    el.songToolResults.replaceChildren();
    let selectionPresent = false;
    tools.items.forEach((song) => {
      const packageName = songPackage(song);
      if (!packageName) return;
      const selected = songPackage(tools.selected) === packageName;
      const row = make('div', 'lh-song-tool-row');
      row.setAttribute('role', 'listitem');
      const item = make('button', 'lh-song-tool-item');
      item.type = 'button';
      item.setAttribute('aria-current', String(selected));
      item.setAttribute('aria-expanded', String(selected));
      item.setAttribute('aria-controls', 'lh-song-tool-selection');
      item.dataset.package = packageName;
      item.appendChild(make('strong', '', song.title || packageName));
      item.appendChild(make(
        'span', 'lh-song-tool-artist', song.artist || 'Unknown artist',
      ));
      item.appendChild(make(
        'span',
        'lh-song-tool-format',
        packageName.toLowerCase().endsWith('.sloppak') ? 'Sloppak' : 'Feedpak',
      ));
      item.addEventListener('click', () => selectSongTool(song));
      row.appendChild(item);
      if (selected) selectionPresent = true;
      el.songToolResults.appendChild(row);
    });
    if (tools.selected && !selectionPresent) {
      tools.selected = null;
      tools.selectionRequest += 1;
      el.songToolSelection.replaceChildren();
      setHidden(el.songToolSelection, true);
    }

    const first = tools.total ? tools.page * songToolPageSize + 1 : 0;
    const last = Math.min(tools.total, (tools.page + 1) * songToolPageSize);
    text(
      el.songToolCount,
      tools.total
        ? `Showing ${number(first)}-${number(last)} of ${number(tools.total)} local songs.`
        : tools.query
          ? 'No local songs match this search.'
          : 'No indexed local songs are available.',
    );
    const pageCount = Math.max(1, Math.ceil(tools.total / songToolPageSize));
    setHidden(el.songToolPagination, tools.total <= songToolPageSize);
    text(el.songToolPage, `Page ${number(tools.page + 1)} of ${number(pageCount)}`);
    el.songToolPrev.disabled = tools.page <= 0;
    el.songToolNext.disabled = tools.page + 1 >= pageCount;
  }

  async function loadSongTools() {
    const requestId = ++state.songTools.requestId;
    setHidden(el.songToolError, true);
    text(el.songToolCount, 'Loading local songs...');
    const params = new URLSearchParams({
      provider: 'local',
      q: state.songTools.query,
      page: String(state.songTools.page),
      size: String(songToolPageSize),
      sort: 'artist',
    });
    try {
      const payload = await coreRequest(`/api/library?${params}`);
      if (
        requestId !== state.songTools.requestId
        || !state.active
        || state.workspace !== 'tools'
      ) return;
      state.songTools.items = Array.isArray(payload?.songs) ? payload.songs : [];
      state.songTools.total = Number(payload?.total || 0);
      state.songTools.loaded = true;
      renderSongToolLibrary();
    } catch (error) {
      if (requestId !== state.songTools.requestId || !state.active) return;
      state.songTools.items = [];
      state.songTools.total = 0;
      closeSongToolSelection({ render: false });
      el.songToolResults.replaceChildren();
      text(el.songToolCount, 'The local song list could not be loaded.');
      text(el.songToolError, error.message);
      setHidden(el.songToolError, false);
      setHidden(el.songToolPagination, true);
    }
  }

  function songToolHeading(song) {
    const packageName = songPackage(song);
    const heading = make('div', 'lh-song-tool-selection-header');
    const title = make('div');
    const titleHeading = make('h3', '', song.title || packageName);
    titleHeading.id = 'lh-song-tool-selection-title';
    title.appendChild(titleHeading);
    title.appendChild(make('p', '', song.artist || 'Unknown artist'));
    heading.appendChild(title);
    heading.appendChild(badge('Selected local song', 'good'));
    return heading;
  }

  function renderPreviewCreator(song, status, region) {
    const packageName = songPackage(song);
    const report = {
      package: packageName,
      title: status.title || song.title || packageName,
      artist: status.artist || song.artist || '',
      features: {
        preview_declared: !!status.preview_declared,
        preview_available: !!status.current_preview_available,
      },
    };
    region.replaceChildren();

    const card = make('section', 'lh-song-tool-card');
    card.appendChild(make('h4', '', 'Preview Creator'));
    card.appendChild(make(
      'p',
      '',
      status.current_preview_available
        ? 'Listen to the current library preview, or create a new 30-second excerpt from the full song mix. This is optional even when the current preview passes Library Doctor checks.'
        : 'Create a standard 30-second library preview from the full song mix. Songs shorter than 30 seconds use the available song length.',
    ));
    if (status.current_preview_available) {
      const currentLabel = make('strong', 'lh-song-tool-current-label', 'Current preview');
      card.appendChild(currentLabel);
      const audio = document.createElement('audio');
      audio.className = 'lh-media-preview-player';
      audio.controls = true;
      audio.preload = 'none';
      audio.src = currentPreviewUrl(packageName);
      audio.setAttribute('aria-label', `Current preview for ${report.title}`);
      card.appendChild(audio);
    }

    if (status.available && status.rule_code) {
      const finding = { code: status.rule_code };
      const actions = make('div', 'lh-repair-buttons');
      const manual = make(
        'button',
        'lh-button lh-button-primary',
        status.current_preview_available
          ? 'Listen and choose a replacement preview'
          : 'Listen and choose a preview',
      );
      const automatic = make(
        'button', 'lh-button', 'Create automatically and finish',
      );
      manual.type = 'button';
      automatic.type = 'button';
      const actionRegion = make('div', 'lh-repair-preview');
      manual.addEventListener('click', () => actionRegistry.previewRepair(
        report, finding, manual, actionRegion,
      ));
      automatic.addEventListener('click', () => actionRegistry.confirmAutomaticPreviewRepair(
        report, finding, automatic, manual, actionRegion,
      ));
      actions.appendChild(manual);
      actions.appendChild(automatic);
      card.appendChild(actions);
      card.appendChild(actionRegion);
    } else {
      card.appendChild(make(
        'p', 'lh-repair-warning', status.message || 'Preview Creator is unavailable for this Feedpak.',
      ));
    }
    region.appendChild(card);
  }

  async function openPreviewCreator(song, trigger, region, { refresh = false } = {}) {
    const packageName = songPackage(song);
    if (!packageName) return;
    if (!refresh && state.songTools.activeTool === 'preview') {
      state.songTools.activeTool = '';
      state.songTools.selectionRequest += 1;
      trigger.setAttribute('aria-expanded', 'false');
      region.replaceChildren();
      setHidden(region, true);
      return;
    }
    const selectionRequest = ++state.songTools.selectionRequest;
    state.songTools.activeTool = 'preview';
    trigger.setAttribute('aria-expanded', 'true');
    setHidden(region, false);
    region.replaceChildren(make('p', 'lh-muted', 'Opening Preview Creator...'));
    try {
      const status = await request(
        `/repair/media/tool/status?package=${encodeURIComponent(packageName)}`,
      );
      if (
        !state.active
        || state.workspace !== 'tools'
        || state.songTools.activeTool !== 'preview'
        || selectionRequest !== state.songTools.selectionRequest
        || songPackage(state.songTools.selected) !== packageName
      ) return;
      renderPreviewCreator(song, status, region);
    } catch (error) {
      if (
        state.songTools.activeTool !== 'preview'
        || selectionRequest !== state.songTools.selectionRequest
        || songPackage(state.songTools.selected) !== packageName
      ) return;
      region.replaceChildren(make('p', 'lh-inline-error', error.message));
    }
  }

  function renderSongToolMenu(song, { openTool = '' } = {}) {
    const packageName = songPackage(song);
    state.songTools.activeTool = '';
    el.songToolSelection.replaceChildren();
    el.songToolSelection.appendChild(songToolHeading(song));
    el.songToolSelection.appendChild(make('p', 'lh-song-tool-path', packageName));
    el.songToolSelection.appendChild(make(
      'p', 'lh-song-tool-menu-label', 'Available tools',
    ));

    const menu = make('div', 'lh-song-tool-menu');
    const preview = make('button', 'lh-song-tool-choice');
    preview.type = 'button';
    preview.setAttribute('aria-expanded', 'false');
    preview.setAttribute('aria-controls', 'lh-song-tool-active');
    preview.appendChild(make('strong', '', 'Preview Creator'));
    preview.appendChild(make(
      'span', '', 'Listen to, replace, or create the short preview used in the song library.',
    ));
    const region = make('div', 'lh-song-tool-active');
    region.id = 'lh-song-tool-active';
    setHidden(region, true);
    preview.addEventListener('click', () => openPreviewCreator(song, preview, region));
    menu.appendChild(preview);
    el.songToolSelection.appendChild(menu);
    el.songToolSelection.appendChild(region);
    setHidden(el.songToolSelection, false);
    focus(el.songToolSelection);
    if (openTool === 'preview') {
      openPreviewCreator(song, preview, region, { refresh: true });
    }
  }

  function closeSongToolSelection({ render = true } = {}) {
    const closedPackage = songPackage(state.songTools.selected);
    state.songTools.selected = null;
    state.songTools.activeTool = '';
    state.songTools.selectionRequest += 1;
    el.songToolSelection.replaceChildren();
    setHidden(el.songToolSelection, true);
    if (render) {
      renderSongToolLibrary();
      const trigger = Array.from(el.songToolResults.querySelectorAll('[data-package]'))
        .find((node) => node.dataset.package === closedPackage);
      focus(trigger);
    }
  }

  function selectSongTool(song) {
    const packageName = songPackage(song);
    if (!packageName) return;
    if (
      songPackage(state.songTools.selected) === packageName
    ) {
      closeSongToolSelection();
      return;
    }
    state.songTools.selectionRequest += 1;
    state.songTools.selected = song;
    renderSongToolLibrary();
    renderSongToolMenu(song);
  }

  function refreshSelectedSongTool(packageName) {
    if (
      state.workspace === 'tools'
      && songPackage(state.songTools.selected) === packageName
    ) {
      renderSongToolMenu(state.songTools.selected, { openTool: 'preview' });
    }
  }


  return {
    closeSongToolSelection,
    loadSongTools,
    refreshSelectedSongTool,
    renderSongToolLibrary,
    setWorkspace,
  };
}
