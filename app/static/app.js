'use strict';

(() => {
  const $ = (id) => document.getElementById(id);
  const terminalStates = new Set(['succeeded', 'failed', 'cancelled']);
  const stateLabels = { queued: '排队中', running: '转换中', succeeded: '已完成', failed: '转换失败', cancelled: '已取消' };
  const categoryLabels = { pdf: 'PDF 工具', document: '文档转换', documents: '文档转换', office: '办公文档', image: '图片处理', images: '图片处理', audio: '音频转换', video: '视频转换', media: '音视频', archive: '压缩与解压', data: '数据与表格', text: '文本转换' };
  const state = {
    tools: [], limits: {}, category: 'all', query: '', selected: null,
    drafts: new Map(), session: { required: false, authenticated: false }, sessionReady: false,
    jobs: [], jobsLoaded: false, submitting: false, pendingJobs: new Set(),
    polling: false, pollTimer: null, jobsSignature: '', sessionEpoch: 0, toastTimer: null
  };

  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined && text !== null) result.textContent = String(text);
    return result;
  }

  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function showMessage(id, message, kind = '') {
    const element = $(id);
    element.textContent = message || '';
    element.hidden = !message;
    if (id === 'global-message') element.className = `notice ${kind ? `notice-${kind}` : ''}`;
  }

  function toast(message) {
    clearTimeout(state.toastTimer);
    $('toast').textContent = message;
    $('toast').hidden = false;
    state.toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4500);
  }

  function canUseWorkspace() {
    return state.sessionReady && (!state.session.required || state.session.authenticated);
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.body instanceof FormData ? 180000 : 20000);
    try {
      const response = await fetch(path, { credentials: 'same-origin', ...options, signal: controller.signal });
      let data = null;
      const body = await response.text();
      if (body) {
        try { data = JSON.parse(body); }
        catch (error) {
          if (response.ok) throw new Error('服务返回了无效数据，请刷新后重试。');
          data = null;
        }
      }
      if (!response.ok) {
        const error = new Error((typeof data?.error === 'string' ? data.error : data?.error?.message) || `请求失败（HTTP ${response.status}），请稍后重试。`);
        error.code = data?.error?.code;
        error.status = response.status;
        if (response.status === 401 && path !== '/api/login') {
          state.session = { required: true, authenticated: false };
          state.sessionEpoch += 1;
          state.jobs = [];
          state.jobsLoaded = false;
          state.jobsSignature = '';
          renderSession();
          renderJobs();
        }
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，请检查网络后重试；若已提交转换，请先刷新任务队列确认。');
      if (error instanceof TypeError) throw new Error('连接服务失败，请检查网络并重试。');
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  function bytes(value) {
    const size = Number(value);
    if (!Number.isFinite(size) || size < 0) return '大小未知';
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function categoryName(category) { return categoryLabels[category] || category || '其他工具'; }
  function categoryIcon(category) {
    const text = String(category).toLowerCase();
    if (/image|图片|图像/.test(text)) return 'image';
    if (/audio|video|media|音|视频/.test(text)) return 'audio';
    if (/archive|压缩/.test(text)) return 'archive';
    return 'file';
  }
  let imageEditor = null;
  let editorFile;
  let editorDraft;
  let inspectTimer = null;
  const isPdfInspect = (tool = state.selected) => ['pdf_split', 'pdf_merge'].includes(tool?.id);
  const isMultiple = (tool = state.selected) => tool?.id !== 'image_resize' && (tool?.id === 'pdf_merge' || Boolean(tool?.multiple));
  const readableError = (error) => typeof error === 'string' ? error : error?.message || '文件读取失败，请检查文件后重试。';
  const optionLabels = { mode: '尺寸模式', background: '画布底色', quality: '图片质量（1–100，越高越清晰）', width: '目标宽度（像素，0 为按比例）', height: '目标高度（像素，0 为按比例）', rotate: '旋转角度（顺时针）', max_kb: '目标体积上限（KB，0 为不限）', dpi: '输出分辨率（DPI）', cad_quality: '图纸输出质量' };
  const choiceLabels = { contain: '等比完整', cover: '居中裁剪铺满', stretch: '拉伸', white: '白色', red: '红色', blue: '蓝色', transparent: '透明', fast: '快速', balanced: '均衡', high: '高质量' };
  function toolOptions(tool) {
    const options = (tool.options || []).map((option) => ({ ...option }));
    const add = (option) => { if (!options.some((existing) => existing.name === option.name)) options.push(option); };
    if (tool.id === 'image_resize') {
      add({ name: 'max_kb', type: 'number', default: 0, min: 0, max: 100000 });
      add({ name: 'dpi', type: 'number', default: 300, min: 72, max: 600 });
      const background = options.find((option) => option.name === 'background');
      if (background) background.choices = [...new Set([...(background.choices || []), 'transparent'])];
    }
    if (tool.id === 'cad_convert') add({ name: 'cad_quality', type: 'select', default: 'balanced', choices: ['fast', 'balanced', 'high'] });
    return options;
  }
  function draft() {
    if (!state.selected) return null;
    if (!state.drafts.has(state.selected.id)) {
      const options = {};
      for (const option of toolOptions(state.selected)) {
        if (option.name !== 'target') options[option.name] = option.default ?? (option.type === 'checkbox' ? false : option.type === 'select' ? option.choices?.[0] || '' : '');
      }
      const targetDefault = state.selected.options?.find((option) => option.name === 'target')?.default;
      const target = state.selected.targets?.includes(targetDefault) ? targetDefault : state.selected.targets?.[0] || '';
      state.drafts.set(state.selected.id, { files: [], fonts: [], target, options, inspections: new Map(), inspectVersion: 0, inspectPending: false, inspectError: '', inspectPassword: null });
    }
    return state.drafts.get(state.selected.id);
  }

  function renderCategories() {
    const container = $('categories');
    container.replaceChildren();
    const categories = new Map();
    for (const tool of state.tools) categories.set(tool.category, (categories.get(tool.category) || 0) + 1);
    const makeCategory = (id, label, count, symbol) => {
      const button = node('button', `category-button${state.category === id ? ' active' : ''}`);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(state.category === id));
      button.append(icon(symbol), node('span', '', label), node('span', 'category-count', count));
      button.addEventListener('click', () => {
        state.category = id;
        renderCategories();
        renderTools();
      });
      container.append(button);
    };
    makeCategory('all', '全部工具', state.tools.length, 'grid');
    for (const [category, count] of categories) makeCategory(category, categoryName(category), count, categoryIcon(category));
    $('tool-total').textContent = `${state.tools.length} 个工具`;
  }

  function renderTools() {
    const query = state.query.toLocaleLowerCase();
    const tools = state.tools.filter((tool) => (state.category === 'all' || tool.category === state.category) &&
      `${tool.name} ${tool.description} ${(tool.inputs || []).join(' ')} ${(tool.targets || []).join(' ')}`.toLocaleLowerCase().includes(query));
    $('tools-title').textContent = state.category === 'all' ? '全部工具' : categoryName(state.category);
    $('tools-summary').textContent = '按用途选择，缺失依赖的工具也会列出';
    $('filtered-count').textContent = `${tools.length} 个工具`;
    const list = $('tool-list');
    list.replaceChildren();
    if (!tools.length) {
      list.append(node('div', 'no-tools', state.tools.length ? '没有匹配的工具，试试搜索 PDF、图片或其他格式。' : '当前服务尚未配置转换工具。'));
      return;
    }
    for (const tool of tools) {
      const selected = state.selected?.id === tool.id;
      const button = node('button', `tool-card${selected ? ' selected' : ''}${tool.available ? '' : ' unavailable'}`);
      button.type = 'button';
      button.setAttribute('aria-pressed', String(selected));
      button.title = tool.available ? tool.description || tool.name : `${tool.name}：${tool.reason || '依赖工具尚未安装'}`;
      const symbol = node('span', 'tool-icon');
      symbol.append(icon(categoryIcon(tool.category)));
      const content = node('span');
      content.append(node('span', 'tool-card-title', tool.name));
      const inputs = (tool.inputs || []).map((v) => v.toUpperCase()).join(' / ');
      const targets = (tool.targets || []).map((v) => v.toUpperCase()).join(' / ');
      content.append(node('span', 'tool-card-meta', `${inputs || '源文件'} → ${targets || '处理结果'}`));
      if (!tool.available) content.append(node('span', 'tool-unavailable-tag', '缺少依赖'));
      button.append(symbol, content);
      if (selected) {
        const check = icon('check');
        check.classList.add('tool-selected-mark');
        button.append(check);
      }
      button.addEventListener('click', () => {
        selectTool(tool);
        $('workspace').scrollIntoView({ behavior: 'auto', block: 'start' });
        $('workspace').focus({ preventScroll: true });
      });
      list.append(button);
    }
  }

  function selectTool(tool) {
    if (state.selected?.id === tool.id) return;
    state.selected = tool;
    const current = draft();
    renderTools();
    $('converter-title').textContent = tool.name;
    $('converter-description').textContent = tool.description || '选择源文件并设置参数后开始转换。';
    $('converter-icon').replaceChildren(icon(categoryIcon(tool.category)));
    $('conversion-form').hidden = false;
    $('tool-availability').hidden = false;
    $('tool-availability').textContent = tool.available ? '可以使用' : '暂不可用';
    $('tool-availability').className = `availability${tool.available ? '' : ' missing'}`;
    showMessage('unavailable-message', tool.available ? '' : `此工具暂不可用：${tool.reason || '依赖工具尚未安装，请联系管理员补全运行环境。'}`);
    const inputs = tool.inputs || [];
    $('accepted-formats').textContent = inputs.length ? `支持 ${inputs.map((v) => v.toUpperCase()).join('、')}` : '按工具要求添加文件';
    $('file-input').accept = inputs.map((v) => `.${v}`).join(',');
    $('file-input').multiple = isMultiple(tool);
    $('file-picker-help').textContent = tool.id === 'pdf_merge' ? '可一次选择多个 PDF：按住 Ctrl / Shift 多选；也可重复选择追加，再用上下箭头排序合并。' : tool.id === 'pdf_split' ? '先上传 PDF 查看真实页数与页面预览，再填写拆分页码范围。' : tool.id === 'image_resize' ? '每次编辑一张图片。裁剪框仅对应当前原图；其他图片转换工具支持批量。' : '或从电脑中选择文件，按住 Ctrl / Shift 可多选并重复追加。';
    $('file-input').value = '';
    const maxCount = isMultiple(tool) ? state.limits.max_files : 1;
    $('upload-limits').textContent = [state.limits.max_file_mb ? `单文件不超过 ${state.limits.max_file_mb} MB` : '', maxCount ? `每次最多 ${maxCount} 个文件` : '', isMultiple(tool) ? '可按顺序批量添加' : '每次处理 1 个文件'].filter(Boolean).join('，');
    const select = $('target-format');
    select.replaceChildren();
    for (const target of tool.targets || []) {
      const option = node('option', '', target.toUpperCase());
      option.value = target;
      select.append(option);
    }
    select.value = current.target;
    $('target-field').hidden = !tool.targets?.length;
    select.required = Boolean(tool.targets?.length);
    renderOptions(tool, current);
    $('pdf-note').hidden = !inputs.some((format) => format.toLowerCase() === 'pdf');
    showMessage('file-error', '');
    showMessage('submit-error', '');
    renderFiles();
  }

  function renderOptions(tool, current) {
    $('tool-options').replaceChildren();
    for (const option of toolOptions(tool)) {
      if (option.name === 'target' || option.name === 'crop') continue;
      const label = node('label', `field${option.type === 'checkbox' ? ' checkbox-field' : ''}`);
      const caption = node('span', '', optionLabels[option.name] || option.label || option.name);
      const input = node(option.type === 'select' ? 'select' : 'input');
      input.name = option.name;
      input.dataset.option = option.name;
      if (option.type === 'select') {
        for (const choice of option.choices || []) {
          const choiceNode = node('option', '', choiceLabels[choice] || choice);
          choiceNode.value = choice;
          input.append(choiceNode);
        }
        input.value = String(current.options[option.name]);
      } else {
        input.type = ['number', 'checkbox'].includes(option.type) ? option.type : /password/i.test(option.name) ? 'password' : 'text';
        if (option.type === 'checkbox') input.checked = Boolean(current.options[option.name]);
        else input.value = String(current.options[option.name]);
        if (option.type === 'number') {
          if (option.min !== undefined && option.min !== null) input.min = option.min;
          if (option.max !== undefined && option.max !== null) input.max = option.max;
          input.step = 'any';
        }
      }
      input.addEventListener(option.type === 'select' ? 'change' : 'input', () => {
        const value = input.type === 'checkbox' ? input.checked : input.type === 'number' && input.value !== '' ? Number(input.value) : input.value;
        current.options[option.name] = value;
        if (tool.id === 'image_resize') {
          if (option.name === 'background' && value === 'transparent' && !['png', 'webp', 'tiff', 'gif', 'ico', 'avif'].includes(current.target)) {
            current.target = 'png'; $('target-format').value = 'png'; toast('透明背景已自动切换为 PNG 格式。');
          }
          imageEditor?.refresh(); updateSubmit();
        }
        if (option.name === 'password' && isPdfInspect(tool)) {
          invalidateInspection(current);
          current.inspections.clear();
          renderInspection(); updateSubmit();
          clearTimeout(inspectTimer);
          inspectTimer = setTimeout(() => inspectFiles(current, tool), 550);
        }
      });
      if (option.name === 'max_kb') label.dataset.compressionField = 'true';
      if (option.type === 'checkbox') label.append(input, caption);
      else label.append(caption, input);
      $('tool-options').append(label);
    }
  }

  function extension(filename) { return String(filename).split('.').pop().toLowerCase(); }
  function addFiles(incoming) {
    if (!state.selected || state.submitting) return;
    const current = draft();
    const errors = [];
    const limit = isMultiple() ? Number(state.limits.max_files) || Infinity : 1;
    const formats = (state.selected.inputs || []).map((value) => value.toLowerCase());
    const maxBytes = Number(state.limits.max_file_mb) * 1024 * 1024;
    const previousCount = current.files.length;
    for (const file of Array.from(incoming)) {
      if (formats.length && !formats.includes(extension(file.name))) { errors.push(`${file.name}：不支持此格式`); continue; }
      if (maxBytes > 0 && file.size > maxBytes) { errors.push(`${file.name}：超过单文件 ${state.limits.max_file_mb} MB 限制`); continue; }
      if (file.size === 0) { errors.push(`${file.name}：文件为空`); continue; }
      if (current.files.some((existing) => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified)) { errors.push(`${file.name}：已在文件列表中`); continue; }
      if (current.files.length >= limit) { errors.push(`每次最多添加 ${limit} 个文件；请移除文件或提交后再添加。`); break; }
      current.files.push(file);
    }
    $('file-input').value = '';
    showMessage('file-error', errors.join('；'));
    if (current.files.length !== previousCount) {
      invalidateInspection(current);
      if (isPdfInspect()) inspectFiles(current, state.selected);
    }
    renderFiles();
  }

  function invalidateInspection(current) {
    current.inspectVersion += 1;
    current.inspectPending = false;
    current.inspectError = '';
    for (const file of current.inspections.keys()) if (!current.files.includes(file)) current.inspections.delete(file);
    if (draft() === current) renderInspection();
  }

  async function inspectFiles(current, tool) {
    if (!isPdfInspect(tool) || !current.files.length) return;
    const version = ++current.inspectVersion;
    const epoch = state.sessionEpoch;
    const files = [...current.files];
    const password = String(current.options.password || '');
    current.inspectPending = true;
    current.inspectError = '';
    if (current.inspectPassword !== password) current.inspections.clear();
    current.inspectPassword = password;
    if (draft() === current) { renderInspection(); updateSubmit(); }
    const body = new FormData();
    for (const file of files) body.append('files', file, file.name);
    if (password) body.append('password', password);
    try {
      const result = await api('/api/inspect', { method: 'POST', body });
      if (version !== current.inspectVersion || epoch !== state.sessionEpoch) return;
      if (result?.error) throw new Error(readableError(result.error));
      if (!Array.isArray(result?.files) || result.files.length !== files.length) throw new Error('PDF 检查返回的数据不完整，请重新读取。');
      files.forEach((file, index) => current.inspections.set(file, result.files[index]));
    } catch (error) {
      if (version !== current.inspectVersion || epoch !== state.sessionEpoch) return;
      current.inspectError = error.message;
    } finally {
      if (version === current.inspectVersion && epoch === state.sessionEpoch) {
        current.inspectPending = false;
        if (draft() === current) { renderInspection(); updateSubmit(); }
      }
    }
  }

  function renderInspection() {
    const current = draft();
    const visible = isPdfInspect() && current?.files.length;
    $('pdf-inspection').hidden = !visible;
    if (!visible) return;
    $('inspect-retry').disabled = current.inspectPending || state.submitting;
    $('inspect-status').textContent = current.inspectPending ? '正在上传并读取真实页数与页面预览…' : current.inspectError || (state.selected.id === 'pdf_split' ? '确认页数后，在下方填写拆分范围；留空逐页拆分。' : '按下面的文件顺序合并，可用文件列表上下箭头调整顺序。');
    $('inspect-status').className = current.inspectError ? 'field-error' : 'editor-help';
    const results = $('inspect-results');
    results.replaceChildren();
    for (const file of current.files) {
      const item = current.inspections.get(file);
      const section = node('article', 'pdf-file-preview');
      section.append(node('h4', '', file.name));
      if (!item) section.append(node('p', 'editor-help', current.inspectPending ? '正在读取…' : '等待读取，请点击重新读取。'));
      else if (item.error || !Number.isInteger(item.pages) || item.pages < 1) section.append(node('p', 'field-error', item.error ? readableError(item.error) : item.encrypted ? '此 PDF 已加密，请填写下方密码后重新读取。' : '此 PDF 未读取到有效页面，请检查文件。'));
      else {
        section.append(node('p', 'pdf-page-count', `${item.pages} 页${item.encrypted ? ' · 已使用密码打开' : ''}`));
        const previews = node('div', 'pdf-preview-grid');
        for (const preview of (Array.isArray(item.previews) ? item.previews : []).slice(0, 6)) {
          if (!/^data:image\/png;base64,[a-zA-Z0-9+/=\s]+$/.test(preview.data_url || '')) continue;
          const figure = node('figure');
          const image = node('img');
          image.src = preview.data_url; image.alt = `${file.name} 第 ${preview.page} 页预览`; image.loading = 'lazy';
          figure.append(image, node('figcaption', '', `第 ${preview.page} 页`)); previews.append(figure);
        }
        if (previews.childElementCount) section.append(previews, node('p', 'editor-help', '最多显示前 6 页预览；拆分范围按原文件页码填写。'));
        else section.append(node('p', 'editor-help', '当前文件没有返回页面缩略图，页数已读取。'));
      }
      results.append(section);
    }
  }

  function renderImageEditor() {
    const current = draft();
    const visible = state.selected?.id === 'image_resize';
    $('image-editor').hidden = !visible;
    if (imageEditor && (!visible || editorDraft !== current || editorFile !== current?.files[0])) {
      imageEditor.destroy(); imageEditor = null; editorDraft = null; editorFile = undefined;
    }
    if (visible && !imageEditor) {
      editorDraft = current; editorFile = current.files[0];
      imageEditor = window.FormatImageEditor.mount($('image-editor'), editorFile, current.options, updateSubmit, (width, height) => {
        current.options.width = width; current.options.height = height;
        for (const [name, value] of [['width', width], ['height', height]]) {
          const input = $('tool-options').querySelector(`[data-option="${name}"]`);
          if (input) input.value = value;
        }
      });
    }
    imageEditor?.setDisabled(state.submitting);
  }

  function renderFonts() {
    const current = draft();
    $('cad-fonts').hidden = state.selected?.id !== 'cad_convert';
    $('fonts-list').replaceChildren();
    for (const [index, file] of (current?.fonts || []).entries()) {
      const row = node('li', 'file-row');
      row.append(node('span', 'file-name', file.name), node('span', 'file-size', bytes(file.size)), fileAction('close', `移除字库 ${file.name}`, () => { current.fonts.splice(index, 1); renderFonts(); }, false, true));
      $('fonts-list').append(row);
    }
    $('fonts-input').disabled = state.submitting;
  }

  function fileAction(symbol, label, onClick, disabled, danger = false) {
    const button = node('button', `icon-button${danger ? ' danger' : ''}`);
    button.type = 'button';
    button.title = label;
    button.setAttribute('aria-label', label);
    button.disabled = Boolean(disabled || state.submitting);
    button.append(icon(symbol));
    button.addEventListener('click', onClick);
    return button;
  }

  function renderFiles() {
    const files = draft()?.files || [];
    $('file-list').replaceChildren();
    files.forEach((file, index) => {
      const row = node('li', 'file-row');
      const data = node('span', 'file-data');
      const name = node('span', 'file-name', file.name);
      name.title = file.name;
      data.append(name, node('span', 'file-size', bytes(file.size)));
      const actions = node('span', 'file-actions');
      actions.append(fileAction('up', `上移 ${file.name}`, () => moveFile(index, -1), index === 0), fileAction('down', `下移 ${file.name}`, () => moveFile(index, 1), index === files.length - 1), fileAction('close', `移除 ${file.name}`, () => {
        files.splice(index, 1);
        const current = draft();
        invalidateInspection(current);
        if (state.selected.id === 'image_resize') delete current.options.crop;
        if (isPdfInspect() && files.some((item) => !current.inspections.has(item))) inspectFiles(current, state.selected);
        showMessage('file-error', '');
        renderFiles();
      }, false, true));
      row.append(node('span', 'file-index', String(index + 1).padStart(2, '0')), icon('file'), data, actions);
      $('file-list').append(row);
    });
    $('files-heading').hidden = !files.length;
    $('files-summary').textContent = `${files.length} 个文件，共 ${bytes(files.reduce((total, file) => total + file.size, 0))}。${state.selected?.id === 'image_resize' ? '当前图片独立编辑。' : '列表顺序即处理 / 合并顺序。'}`;
    renderInspection();
    renderImageEditor();
    renderFonts();
    updateSubmit();
  }

  function moveFile(index, offset) {
    const files = draft().files;
    const next = index + offset;
    if (next < 0 || next >= files.length || state.submitting) return;
    [files[index], files[next]] = [files[next], files[index]];
    renderFiles();
    const action = $('file-list').children[next]?.querySelector(offset < 0 ? '[aria-label^="上移"]' : '[aria-label^="下移"]');
    if (action && !action.disabled) action.focus();
    toast(`已调整文件顺序：${files[next].name}`);
  }

  function updateSubmit() {
    const current = draft();
    const count = current?.files.length || 0;
    const inspectBlocked = isPdfInspect() && (current?.inspectPending || !count || current.files.some((file) => { const item = current.inspections.get(file); return !item || item.error || !Number.isInteger(item.pages) || item.pages < 1; }));
    $('convert-button').disabled = !canUseWorkspace() || !state.selected?.available || !count || state.submitting || inspectBlocked;
    $('convert-label').textContent = state.submitting ? '正在提交…' : '开始转换';
    $('submit-summary').textContent = state.submitting ? '正在上传并创建任务，请稍候' : count ? `已准备 ${count} 个文件` : '添加文件后即可开始';
    $('submit-help').textContent = !state.sessionReady ? '正在确认会话状态。' : !canUseWorkspace() ? '请先登录工作台。' : !state.selected?.available ? '补全此工具的依赖后即可使用。' : '转换会在后台运行，可继续处理其他文件。';
    $('file-input').disabled = state.submitting;
    $('clear-files').disabled = state.submitting;
    $('target-format').disabled = state.submitting;
    for (const input of $('tool-options').querySelectorAll('input,select')) input.disabled = state.submitting;
    const compression = $('tool-options').querySelector('[data-compression-field]');
    if (compression) {
      compression.hidden = !['jpg', 'jpeg', 'webp'].includes(current?.target);
      compression.querySelector('input').disabled = state.submitting || compression.hidden;
    }
    imageEditor?.setDisabled(state.submitting);
    $('fonts-input').disabled = state.submitting;
    if (inspectBlocked && count && !state.submitting) $('submit-help').textContent = current.inspectPending ? '请等待 PDF 页数与预览读取完成。' : '请先成功读取 PDF；加密文件请填写密码并重新读取。';
  }

  async function submitJob(event) {
    event.preventDefault();
    if (state.submitting || !canUseWorkspace() || !state.selected?.available || !draft()?.files.length) return;
    if (!$('conversion-form').reportValidity() || $('convert-button').disabled) return;
    const current = draft();
    const selected = state.selected;
    const submittedFiles = [...current.files];
    const body = new FormData();
    for (const file of submittedFiles) body.append('files', file, file.name);
    if (selected.id === 'cad_convert') for (const file of current.fonts) body.append('fonts', file, file.name);
    body.append('operation', selected.id);
    const options = { ...current.options, target: current.target };
    if (!['jpg', 'jpeg', 'webp'].includes(current.target)) delete options.max_kb;
    body.append('options', JSON.stringify(options));
    state.submitting = true;
    showMessage('submit-error', '');
    updateSubmit();
    renderFiles();
    try {
      const result = await api('/api/jobs', { method: 'POST', body });
      current.files = [];
      delete current.options.crop;
      invalidateInspection(current);
      toast('转换任务已加入队列，可继续添加其他文件。');
      if (result?.id) {
        state.jobs = [result, ...state.jobs.filter((job) => job.id !== result.id)];
        state.jobsLoaded = true;
        renderJobs();
      }
      await refreshJobs();
    } catch (error) {
      showMessage('submit-error', error.message);
    } finally {
      state.submitting = false;
      renderFiles();
    }
  }

  function renderSession() {
    const required = state.session.required && !state.session.authenticated;
    $('login-panel').hidden = !required;
    $('session-button').hidden = !state.session.required || !state.session.authenticated;
    $('queue-subtitle').textContent = required ? '登录后查看当前会话的任务' : '仅显示当前会话的任务';
    updateSubmit();
  }

  async function loadSession() {
    const result = await api('/api/session');
    if (!result || typeof result.required !== 'boolean' || typeof result.authenticated !== 'boolean') throw new Error('会话状态数据不完整，请刷新重试。');
    state.session = result;
    state.sessionReady = true;
    renderSession();
  }

  async function login(event) {
    event.preventDefault();
    const button = $('login-submit');
    if (button.disabled) return;
    const token = $('access-token').value.trim();
    if (!token) return;
    button.disabled = true;
    button.textContent = '正在登录…';
    showMessage('login-error', '');
    try {
      await api('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
      state.sessionEpoch += 1;
      await loadSession();
      $('access-token').value = '';
      toast('已进入团队工作台。');
      await refreshJobs();
    } catch (error) {
      showMessage('login-error', error.message);
    } finally {
      button.disabled = false;
      button.textContent = '登录工作台';
    }
  }

  async function logout() {
    if (state.submitting) { toast('正在提交文件，请等待提交结束后再退出。'); return; }
    const button = $('session-button');
    button.disabled = true;
    try {
      await api('/api/logout', { method: 'POST' });
      state.sessionEpoch += 1;
      state.jobs = [];
      state.jobsSignature = '';
      state.jobsLoaded = false;
      state.drafts.clear();
      const selected = state.selected;
      state.selected = null;
      if (selected) selectTool(selected);
      renderJobs();
      await loadSession();
      toast('已退出当前会话。');
    } catch (error) {
      showMessage('global-message', error.message, 'error');
    } finally {
      button.disabled = false;
    }
  }

  function formatTime(value) {
    if (value === null || value === undefined || value === '') return '';
    const date = new Date(typeof value === 'number' && value < 1e12 ? value * 1000 : value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date);
  }

  function safeDownloadUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    try {
      const url = new URL(value, window.location.origin);
      if (url.origin !== window.location.origin || !['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
      return `${url.pathname}${url.search}${url.hash}`;
    } catch (error) { return null; }
  }

  function jobButton(text, id, action, danger = false) {
    const button = node('button', danger ? 'text-button' : 'button button-secondary', text);
    button.type = 'button';
    button.dataset.jobAction = `${id}:${action}`;
    button.disabled = state.pendingJobs.has(id);
    button.addEventListener('click', () => changeJob(id, action));
    return button;
  }

  function renderJob(job) {
    const card = node('article', 'job-card');
    const heading = node('div', 'job-heading');
    heading.append(node('h3', '', job.tool_name || job.operation || '转换任务'), node('span', `job-state ${job.state}`, stateLabels[job.state] || '状态未知'));
    const files = Array.isArray(job.files) ? job.files : [];
    const meta = node('div', 'job-meta');
    meta.append(node('span', '', formatTime(job.created_at)), node('span', '', `${files.length} 个文件`));
    card.append(heading, meta);
    const filenames = files.map((file) => file.name).join('、');
    const names = node('p', 'job-files', filenames);
    names.title = filenames;
    card.append(names);
    if (job.state === 'running' || job.state === 'queued') {
      const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
      const bar = node('div', 'job-progress');
      bar.setAttribute('role', 'progressbar');
      bar.setAttribute('aria-label', `${job.tool_name || '转换任务'}进度`);
      bar.setAttribute('aria-valuemin', '0');
      bar.setAttribute('aria-valuemax', '100');
      bar.setAttribute('aria-valuenow', String(progress));
      const fill = node('span');
      fill.style.width = `${progress}%`;
      bar.append(fill);
      const progressLabel = node('div', 'job-progress-label');
      progressLabel.append(node('span', '', job.state === 'queued' ? '等待工作进程处理' : '正在处理文件'), node('span', '', `${Math.round(progress)}%`));
      card.append(bar, progressLabel);
    }
    if (job.error) card.append(node('p', 'job-error', typeof job.error === 'string' ? job.error : job.error.message || '转换失败，请检查源文件与参数后重试。'));
    for (const warning of job.warnings || []) card.append(node('p', 'job-warning', `提示：${warning}`));
    if (job.state === 'failed' && !job.error) card.append(node('p', 'job-error', '转换失败。请检查源文件、目标格式及参数后重试。'));
    const outputs = Array.isArray(job.outputs) ? job.outputs : [];
    if (outputs.length) {
      const list = node('ul', 'output-list');
      for (const output of outputs) {
        const li = node('li');
        const href = safeDownloadUrl(output.url);
        if (href) {
          const link = node('a', 'output-link');
          link.href = href;
          link.setAttribute('download', output.name || '');
          link.append(icon('download'), node('span', '', output.name || '下载结果'), node('span', '', bytes(output.size)));
          li.append(link);
        } else li.append(node('span', 'job-error', '下载地址无效，请刷新任务或联系管理员。'));
        list.append(li);
      }
      card.append(list);
    }
    const actions = node('div', 'job-actions');
    if (job.state === 'queued' || job.state === 'running') actions.append(jobButton('取消任务', job.id, 'cancel', true));
    if (job.state === 'succeeded' && outputs.length) {
      const bundle = node('a', 'button button-secondary', '打包 ZIP');
      bundle.href = `/api/jobs/${encodeURIComponent(job.id)}/bundle`;
      bundle.setAttribute('download', '');
      bundle.prepend(icon('archive'));
      actions.append(bundle);
    }
    if (terminalStates.has(job.state)) actions.append(jobButton('删除记录', job.id, 'delete', true));
    if (actions.childElementCount) card.append(actions);
    return card;
  }

  function renderJobs() {
    const jobs = state.jobs;
    const active = jobs.filter((job) => ['queued', 'running'].includes(job.state)).length;
    $('header-active-count').textContent = active;
    $('queue-total').textContent = jobs.length;
    $('stat-active').textContent = active;
    $('stat-success').textContent = jobs.filter((job) => job.state === 'succeeded').length;
    $('stat-failed').textContent = jobs.filter((job) => job.state === 'failed').length;
    $('clear-finished').hidden = !jobs.some((job) => terminalStates.has(job.state));
    const signature = JSON.stringify(jobs) + String(canUseWorkspace()) + String(state.jobsLoaded) + [...state.pendingJobs].join(',');
    if (signature === state.jobsSignature) return;
    state.jobsSignature = signature;
    const focused = document.activeElement?.dataset?.jobAction;
    $('job-list').replaceChildren();
    if (!jobs.length) {
      const empty = node('div', 'queue-empty');
      empty.append(icon('clock'), node('h3', '', canUseWorkspace() ? '转换任务在这里集合' : '登录后查看转换任务'), node('p', '', canUseWorkspace() ? '添加文件并开始转换，完成后即可下载结果。' : '任务和下载结果仅向当前会话开放。'));
      $('job-list').append(empty);
    } else {
      for (const job of jobs) $('job-list').append(renderJob(job));
    }
    if (focused) {
      const button = Array.from($('job-list').querySelectorAll('[data-job-action]')).find((item) => item.dataset.jobAction === focused);
      if (button) button.focus({ preventScroll: true });
    }
  }

  async function refreshJobs() {
    if (!canUseWorkspace() || state.polling) return;
    const epoch = state.sessionEpoch;
    state.polling = true;
    $('refresh-jobs').disabled = true;
    try {
      const result = await api('/api/jobs');
      if (epoch !== state.sessionEpoch) return;
      if (!Array.isArray(result?.jobs)) throw new Error('任务数据不完整，请刷新重试。');
      state.jobs = [...result.jobs].sort((a, b) => (Date.parse(b.created_at) || 0) - (Date.parse(a.created_at) || 0));
      state.jobsLoaded = true;
      showMessage('jobs-error', '');
      $('poll-status').textContent = '每 2 秒自动更新';
      renderJobs();
    } catch (error) {
      if (epoch !== state.sessionEpoch) return;
      showMessage('jobs-error', error.message);
      $('poll-status').textContent = '更新失败，正在重试';
    } finally {
      state.polling = false;
      $('refresh-jobs').disabled = false;
    }
  }

  async function changeJob(id, action) {
    if (state.pendingJobs.has(id)) return;
    if (action === 'delete' && !window.confirm('删除此任务记录和转换文件？已下载到电脑的文件不受影响。')) return;
    state.pendingJobs.add(id);
    renderJobs();
    try {
      await api(`/api/jobs/${encodeURIComponent(id)}${action === 'cancel' ? '/cancel' : ''}`, { method: action === 'cancel' ? 'POST' : 'DELETE' });
      if (action === 'delete') state.jobs = state.jobs.filter((job) => job.id !== id);
      toast(action === 'cancel' ? '已发送取消请求，任务状态即将更新。' : '已删除任务记录和文件。');
      await refreshJobs();
    } catch (error) {
      showMessage('jobs-error', error.message);
    } finally {
      state.pendingJobs.delete(id);
      renderJobs();
    }
  }

  async function clearFinished() {
    if ($('clear-finished').disabled) return;
    const jobs = state.jobs.filter((job) => terminalStates.has(job.state));
    if (!jobs.length || !window.confirm(`清除 ${jobs.length} 条已结束的任务及其转换文件？请先下载需要保留的结果。`)) return;
    const button = $('clear-finished');
    button.disabled = true;
    const errors = [];
    for (const job of jobs) {
      if (state.pendingJobs.has(job.id)) continue;
      state.pendingJobs.add(job.id);
      try {
        await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: 'DELETE' });
        state.jobs = state.jobs.filter((item) => item.id !== job.id);
      } catch (error) { errors.push(error.message); }
      finally { state.pendingJobs.delete(job.id); }
    }
    renderJobs();
    button.disabled = false;
    if (errors.length) showMessage('jobs-error', `部分记录未清除：${[...new Set(errors)].join('；')}`);
    else toast('已清除结束的任务记录和文件。');
    await refreshJobs();
  }

  async function loadCatalog() {
    try {
      const result = await api('/api/catalog');
      if (!Array.isArray(result?.tools)) throw new Error('工具目录数据不完整，请重新加载。');
      state.tools = result.tools;
      state.limits = result.limits || {};
      renderCategories();
      renderTools();
      const initial = state.tools.find((tool) => tool.id === 'pdf_docx') || state.tools.find((tool) => tool.available) || state.tools[0];
      if (initial) selectTool(initial);
      $('version-label').textContent = `格式工坊${result.version ? ` v${result.version}` : ''}${state.limits.workers ? ` / ${state.limits.workers} 个处理进程` : ''}`;
      if (state.limits.retention_hours) $('retention-note').textContent = `转换文件保留 ${state.limits.retention_hours} 小时，请及时下载需要的结果。`;
    } catch (error) {
      const empty = node('div', 'no-tools');
      empty.append(node('p', '', `工具目录加载失败：${error.message}`));
      const retry = node('button', 'button button-secondary', '重新加载工具');
      retry.type = 'button';
      retry.style.marginTop = '12px';
      retry.addEventListener('click', () => { retry.disabled = true; loadCatalog(); });
      empty.append(retry);
      $('tool-list').replaceChildren(empty);
      $('tools-summary').textContent = '目录暂未就绪';
    }
  }

  async function checkHealth() {
    try {
      const result = await api('/api/health');
      const status = result?.status;
      const healthy = result?.ok !== false && (!status || ['ok', 'healthy', 'ready', 'online'].includes(String(status).toLowerCase()));
      $('health-dot').className = `status-dot ${healthy ? 'online' : 'offline'}`;
      $('health-text').textContent = healthy ? '转换服务已就绪' : '服务状态需关注';
      $('health-text').title = result?.message || (status ? `服务状态：${status}` : '服务连接正常');
    } catch (error) {
      $('health-dot').className = 'status-dot offline';
      $('health-text').textContent = '服务连接异常';
      $('health-text').title = error.message;
    }
  }

  async function poll() {
    await refreshJobs();
    state.pollTimer = setTimeout(poll, 2000);
  }

  async function initialize() {
    const results = await Promise.allSettled([loadSession(), loadCatalog(), checkHealth()]);
    if (results[0].status === 'rejected') {
      showMessage('global-message', `会话初始化失败：${results[0].reason.message} 请刷新页面重试。`, 'error');
      $('poll-status').textContent = '会话未就绪';
      updateSubmit();
    }
    await poll();
  }

  $('tool-search').addEventListener('input', (event) => { state.query = event.target.value.trim(); renderTools(); });
  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName) && !event.ctrlKey && !event.metaKey && !event.altKey) { event.preventDefault(); $('tool-search').focus(); }
  });
  $('file-input').addEventListener('change', (event) => addFiles(event.target.files));
  $('target-format').addEventListener('change', (event) => { if (draft()) { const current = draft(); current.target = event.target.value; if (state.selected.id === 'image_resize' && current.options.background === 'transparent' && !['png', 'webp', 'tiff', 'gif', 'ico', 'avif'].includes(current.target)) { current.options.background = 'white'; const input = $('tool-options').querySelector('[data-option="background"]'); if (input) input.value = 'white'; toast('此格式使用白色画布底色；透明背景请选择 PNG/WebP 等格式。'); } updateSubmit(); imageEditor?.refresh(); } });
  $('inspect-retry').addEventListener('click', () => { if (draft() && !state.submitting) inspectFiles(draft(), state.selected); });
  $('fonts-input').addEventListener('change', (event) => {
    if (!draft() || state.submitting) return;
    const current = draft();
    const errors = [];
    for (const file of Array.from(event.target.files)) {
      if (!['ttf', 'ttc', 'otf', 'shx'].includes(extension(file.name))) { errors.push(`${file.name}：请选择直接字体文件`); continue; }
      if (!file.size || file.size > (Number(state.limits.max_file_mb) || 100) * 1024 * 1024) { errors.push(`${file.name}：字体为空或超过单文件上传限制`); continue; }
      if (current.fonts.some((item) => item.name === file.name && item.size === file.size && item.lastModified === file.lastModified)) continue;
      if (current.fonts.length >= (Number(state.limits.max_files) || 20)) { errors.push('字库数量已达上传上限，请移除后重选。'); break; }
      current.fonts.push(file);
    }
    event.target.value = '';
    showMessage('fonts-error', errors.join('；'));
    renderFonts();
  });
  $('clear-files').addEventListener('click', () => { if (draft() && !state.submitting) { const current = draft(); current.files = []; delete current.options.crop; invalidateInspection(current); showMessage('file-error', ''); renderFiles(); } });
  $('conversion-form').addEventListener('submit', submitJob);
  $('login-form').addEventListener('submit', login);
  $('session-button').addEventListener('click', logout);
  $('refresh-jobs').addEventListener('click', refreshJobs);
  $('clear-finished').addEventListener('click', clearFinished);
  let dragDepth = 0;
  const zone = $('drop-zone');
  zone.addEventListener('dragenter', (event) => { event.preventDefault(); dragDepth += 1; zone.classList.add('drag-over'); });
  zone.addEventListener('dragover', (event) => { event.preventDefault(); if (event.dataTransfer) event.dataTransfer.dropEffect = state.submitting ? 'none' : 'copy'; });
  zone.addEventListener('dragleave', (event) => { event.preventDefault(); dragDepth -= 1; if (dragDepth <= 0) zone.classList.remove('drag-over'); });
  zone.addEventListener('drop', (event) => { event.preventDefault(); dragDepth = 0; zone.classList.remove('drag-over'); if (event.dataTransfer) addFiles(event.dataTransfer.files); });
  window.addEventListener('dragover', (event) => event.preventDefault());
  window.addEventListener('drop', (event) => event.preventDefault());
  window.addEventListener('pagehide', () => { clearTimeout(state.pollTimer); clearTimeout(state.toastTimer); clearTimeout(inspectTimer); imageEditor?.destroy(); imageEditor = null; });
  window.addEventListener('pageshow', (event) => { if (event.persisted) { renderImageEditor(); poll(); } });
  initialize();
})();
