'use strict';

// Crop coordinates always refer to the EXIF-oriented source, before rotation.
window.FormatImageEditor = (() => {
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const full = () => ({ x: 0, y: 0, width: 1, height: 1 });
  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text) result.textContent = text;
    return result;
  }
  function dimensions(sourceWidth, sourceHeight, options) {
    const crop = options.crop || full();
    const width = sourceWidth * crop.width;
    const height = sourceHeight * crop.height;
    const radians = number(options.rotate) * Math.PI / 180;
    const cosine = Math.abs(Math.cos(radians));
    const sine = Math.abs(Math.sin(radians));
    const rotatedWidth = Math.max(1, Math.ceil(width * cosine + height * sine - 1e-8));
    const rotatedHeight = Math.max(1, Math.ceil(width * sine + height * cosine - 1e-8));
    const targetWidth = Math.max(0, Math.round(number(options.width, 600)));
    const targetHeight = Math.max(0, Math.round(number(options.height, 800)));
    return {
      width: targetWidth || Math.max(1, Math.round(rotatedWidth * targetHeight / rotatedHeight)),
      height: targetHeight || Math.max(1, Math.round(rotatedHeight * targetWidth / rotatedWidth)),
      rotatedWidth, rotatedHeight
    };
  }
  function mount(container, file, options, onChange, onPreset) {
    let disposed = false;
    let source = null;
    let disabled = false;
    let drag = null;
    let ratio = 0;
    let url = null;
    let frame = null;
    const root = element('section', 'image-editor');
    root.setAttribute('aria-label', '图片实时预览与裁剪');
    const heading = element('div', 'editor-heading');
    heading.append(element('h3', '', '图片预览与裁剪'), element('span', 'editor-badge', '单图编辑'));
    const intro = element('p', 'editor-help', '在原图上框选裁剪区域，拖动框内移动，拖动右下角调整。裁剪先于旋转和尺寸调整。');
    const presets = element('div', 'editor-presets');
    const presetList = [['一寸 · 295 × 413', 295, 413], ['二寸 · 413 × 579', 413, 579], ['头像 · 512 × 512', 512, 512]];
    for (const [label, width, height] of presetList) {
      const button = element('button', 'button button-secondary', label);
      button.type = 'button';
      button.addEventListener('click', () => { onPreset(width, height); schedule(); });
      presets.append(button);
    }
    const status = element('p', 'editor-status', '正在读取原图…');
    status.setAttribute('role', 'status');
    const views = element('div', 'editor-views');
    const originalView = element('div', 'editor-view');
    const resultView = element('div', 'editor-view');
    const sourceCanvas = element('canvas', 'crop-canvas');
    sourceCanvas.tabIndex = 0;
    sourceCanvas.setAttribute('aria-label', '原图裁剪框。方向键移动裁剪框，Shift 加速；也可使用下方数字输入。');
    const outputCanvas = element('canvas', 'result-canvas');
    outputCanvas.setAttribute('aria-label', '根据当前裁剪、旋转、尺寸和底色生成的结果预览');
    const sourceLabel = element('p', 'preview-label', '原图 · 框选裁剪');
    const outputLabel = element('p', 'preview-label', '预计输出');
    const sourceStage = element('div', 'preview-stage');
    const resultStage = element('div', 'preview-stage');
    sourceStage.append(sourceCanvas);
    resultStage.append(outputCanvas);
    originalView.append(sourceLabel, sourceStage);
    resultView.append(outputLabel, resultStage);
    views.append(originalView, resultView);
    const controls = element('div', 'crop-controls');
    const ratioLabel = element('label', 'field');
    ratioLabel.append(element('span', '', '裁剪比例'));
    const ratioInput = element('select');
    ratioInput.setAttribute('aria-label', '裁剪比例');
    for (const [value, label] of [['0', '自由比例'], ['1', '正方形 1:1'], ['0.7142857143', '一寸 5:7'], ['0.713298791', '二寸 413:579'], ['1.3333333333', '横向 4:3'], ['0.75', '竖向 3:4']]) {
      const option = element('option', '', label);
      option.value = value;
      ratioInput.append(option);
    }
    ratioLabel.append(ratioInput);
    controls.append(ratioLabel);
    const coordinateInputs = {};
    for (const [key, label] of [['x', '左边距 %'], ['y', '上边距 %'], ['width', '裁剪宽度 %'], ['height', '裁剪高度 %']]) {
      const field = element('label', 'field');
      const input = element('input');
      input.type = 'number'; input.min = key === 'x' || key === 'y' ? '0' : '0.1'; input.max = '100'; input.step = '0.1';
      input.setAttribute('aria-label', label);
      field.append(element('span', '', label), input);
      coordinateInputs[key] = input;
      input.addEventListener('change', () => {
        const rect = { ...(options.crop || full()) };
        rect[key] = number(input.value) / 100;
        if (key === 'x' || key === 'y') rect[key] = clamp(rect[key], 0, 1 - rect[key === 'x' ? 'width' : 'height']);
        else rect[key] = clamp(rect[key], 0.001, 1 - rect[key === 'width' ? 'x' : 'y']);
        if (ratio && source && (key === 'width' || key === 'height')) constrain(rect, key);
        setCrop(rect);
      });
      controls.append(field);
    }
    const reset = element('button', 'button button-secondary', '重置裁剪');
    reset.type = 'button';
    reset.addEventListener('click', () => { delete options.crop; ratio = 0; ratioInput.value = '0'; syncCoordinates(); onChange(); schedule(); });
    controls.append(reset);
    ratioInput.addEventListener('change', () => {
      ratio = Number(ratioInput.value);
      if (!source || !ratio) return;
      const rect = { ...(options.crop || full()) };
      constrain(rect, 'width');
      rect.x = clamp(rect.x, 0, 1 - rect.width);
      rect.y = clamp(rect.y, 0, 1 - rect.height);
      setCrop(rect);
    });
    root.append(heading, intro, presets, status, views, controls, element('p', 'editor-help', '预览按比例缩小显示；压缩体积以实际转换结果为准。白、红、蓝底只填充画布和透明区域，不自动抠图。浏览器显示静态首帧。'));
    container.replaceChildren(root);

    function constrain(rect, changed) {
      if (changed === 'height') rect.width = rect.height * source.naturalHeight * ratio / source.naturalWidth;
      else rect.height = rect.width * source.naturalWidth / (source.naturalHeight * ratio);
      const scale = Math.min(1, (1 - rect.x) / rect.width, (1 - rect.y) / rect.height);
      rect.width = Math.max(0.001, rect.width * scale);
      rect.height = Math.max(0.001, rect.height * scale);
    }
    function setCrop(rect) {
      const x = clamp(rect.x, 0, 0.999);
      const y = clamp(rect.y, 0, 0.999);
      options.crop = { x, y, width: clamp(rect.width, 0.001, 1 - x), height: clamp(rect.height, 0.001, 1 - y) };
      syncCoordinates(); onChange(); schedule();
    }
    function syncCoordinates() {
      const rect = options.crop || full();
      for (const [key, input] of Object.entries(coordinateInputs)) input.value = String(Math.round(rect[key] * 10000) / 100);
    }
    function pointer(event) {
      const bounds = sourceCanvas.getBoundingClientRect();
      return { x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1), y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1) };
    }
    sourceCanvas.addEventListener('pointerdown', (event) => {
      if (!source || disabled || event.button !== 0) return;
      event.preventDefault();
      const point = pointer(event);
      const rect = { ...(options.crop || full()) };
      const bounds = sourceCanvas.getBoundingClientRect();
      const corner = Math.abs(point.x - rect.x - rect.width) * bounds.width < 22 && Math.abs(point.y - rect.y - rect.height) * bounds.height < 22;
      const inside = options.crop && point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height;
      drag = { start: point, rect, mode: corner ? 'resize' : inside ? 'move' : 'draw' };
      sourceCanvas.setPointerCapture(event.pointerId);
    });
    sourceCanvas.addEventListener('pointermove', (event) => {
      if (!drag || disabled) return;
      const point = pointer(event);
      let rect;
      if (drag.mode === 'move') {
        rect = { ...drag.rect, x: clamp(drag.rect.x + point.x - drag.start.x, 0, 1 - drag.rect.width), y: clamp(drag.rect.y + point.y - drag.start.y, 0, 1 - drag.rect.height) };
      } else if (drag.mode === 'resize') {
        rect = { ...drag.rect, width: Math.max(0.001, point.x - drag.rect.x), height: Math.max(0.001, point.y - drag.rect.y) };
        if (ratio) constrain(rect, 'width');
      } else {
        rect = { x: Math.min(point.x, drag.start.x), y: Math.min(point.y, drag.start.y), width: Math.max(0.001, Math.abs(point.x - drag.start.x)), height: Math.max(0.001, Math.abs(point.y - drag.start.y)) };
        if (ratio) constrain(rect, 'width');
      }
      setCrop(rect);
    });
    const finishDrag = () => { drag = null; };
    sourceCanvas.addEventListener('pointerup', finishDrag);
    sourceCanvas.addEventListener('pointercancel', finishDrag);
    sourceCanvas.addEventListener('lostpointercapture', finishDrag);
    sourceCanvas.addEventListener('keydown', (event) => {
      if (!source || disabled || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const rect = { ...(options.crop || full()) };
      const step = event.shiftKey ? 0.01 : 0.001;
      if (event.key === 'ArrowLeft') rect.x = clamp(rect.x - step, 0, 1 - rect.width);
      if (event.key === 'ArrowRight') rect.x = clamp(rect.x + step, 0, 1 - rect.width);
      if (event.key === 'ArrowUp') rect.y = clamp(rect.y - step, 0, 1 - rect.height);
      if (event.key === 'ArrowDown') rect.y = clamp(rect.y + step, 0, 1 - rect.height);
      setCrop(rect);
    });
    function schedule() { if (!disposed && frame === null) frame = requestAnimationFrame(() => { frame = null; draw(); }); }
    function draw() {
      if (!source || disposed) return;
      const sw = source.naturalWidth;
      const sh = source.naturalHeight;
      const rect = options.crop || full();
      const sourceScale = Math.min(1, 1000 / sw, 700 / sh);
      sourceCanvas.width = Math.max(1, Math.round(sw * sourceScale));
      sourceCanvas.height = Math.max(1, Math.round(sh * sourceScale));
      sourceCanvas.style.width = `${Math.min(sourceCanvas.width, 340 * sw / sh)}px`;
      const context = sourceCanvas.getContext('2d');
      context.drawImage(source, 0, 0, sourceCanvas.width, sourceCanvas.height);
      const x = rect.x * sourceCanvas.width;
      const y = rect.y * sourceCanvas.height;
      const w = rect.width * sourceCanvas.width;
      const h = rect.height * sourceCanvas.height;
      context.fillStyle = 'rgba(16,46,70,.48)';
      context.beginPath(); context.rect(0, 0, sourceCanvas.width, sourceCanvas.height); context.rect(x, y, w, h); context.fill('evenodd');
      context.strokeStyle = '#fff'; context.lineWidth = 2; context.strokeRect(x + 1, y + 1, Math.max(0, w - 2), Math.max(0, h - 2));
      context.strokeStyle = 'rgba(255,255,255,.55)'; context.lineWidth = 1;
      for (let i = 1; i <= 2; i += 1) { context.beginPath(); context.moveTo(x + w * i / 3, y); context.lineTo(x + w * i / 3, y + h); context.moveTo(x, y + h * i / 3); context.lineTo(x + w, y + h * i / 3); context.stroke(); }
      context.fillStyle = '#087f8c'; context.fillRect(x + w - 10, y + h - 10, 10, 10);
      const dims = dimensions(sw, sh, options);
      sourceLabel.textContent = `原图 ${sw} × ${sh} 像素 · 裁剪 ${Math.max(1, Math.round(sw * rect.width))} × ${Math.max(1, Math.round(sh * rect.height))}`;
      outputLabel.textContent = `预计输出 ${dims.width} × ${dims.height} 像素`;
      if ((!number(options.width) && !number(options.height)) || dims.width * dims.height > 60000000) {
        status.textContent = '请设置有效目标尺寸，宽高至少一项大于 0，总像素不超过 6000 万。';
        outputCanvas.width = 1; outputCanvas.height = 1; return;
      }
      status.textContent = '实时预览 · 裁剪 → 旋转 → 尺寸与底色';
      const scale = Math.min(1, 900 / dims.width, 700 / dims.height);
      outputCanvas.width = Math.max(1, Math.round(dims.width * scale));
      outputCanvas.height = Math.max(1, Math.round(dims.height * scale));
      outputCanvas.style.width = `${Math.min(outputCanvas.width, 340 * dims.width / dims.height)}px`;
      const result = outputCanvas.getContext('2d');
      const bg = { white: '#ffffff', red: '#d9001b', blue: '#438edb', transparent: 'transparent' }[options.background] || '#ffffff';
      if (bg !== 'transparent') { result.fillStyle = bg; result.fillRect(0, 0, outputCanvas.width, outputCanvas.height); }
      const rotated = document.createElement('canvas');
      const rotationScale = Math.min(1, 1400 / dims.rotatedWidth, 1400 / dims.rotatedHeight);
      rotated.width = Math.max(1, Math.round(dims.rotatedWidth * rotationScale));
      rotated.height = Math.max(1, Math.round(dims.rotatedHeight * rotationScale));
      const rotationContext = rotated.getContext('2d');
      rotationContext.translate(rotated.width / 2, rotated.height / 2);
      rotationContext.rotate(number(options.rotate) * Math.PI / 180);
      rotationContext.drawImage(source, rect.x * sw, rect.y * sh, rect.width * sw, rect.height * sh, -rect.width * sw * rotationScale / 2, -rect.height * sh * rotationScale / 2, rect.width * sw * rotationScale, rect.height * sh * rotationScale);
      let drawWidth = outputCanvas.width;
      let drawHeight = outputCanvas.height;
      if (options.mode !== 'stretch') {
        const factor = options.mode === 'cover' ? Math.max(drawWidth / rotated.width, drawHeight / rotated.height) : Math.min(drawWidth / rotated.width, drawHeight / rotated.height);
        drawWidth = rotated.width * factor; drawHeight = rotated.height * factor;
      }
      result.drawImage(rotated, (outputCanvas.width - drawWidth) / 2, (outputCanvas.height - drawHeight) / 2, drawWidth, drawHeight);
      if (options.watermark) {
        result.font = `${Math.max(12, Math.min(dims.width, dims.height) / 18) * scale}px sans-serif`;
        result.textAlign = 'right'; result.textBaseline = 'alphabetic'; result.fillStyle = 'rgba(40,40,40,.63)';
        result.fillText(String(options.watermark), outputCanvas.width - 12 * scale, outputCanvas.height - 12 * scale);
      }
    }
    syncCoordinates();
    if (file) {
      url = URL.createObjectURL(file);
      source = new Image();
      source.onload = () => { if (!disposed) schedule(); };
      source.onerror = () => {
        if (!disposed) { source = null; status.textContent = '浏览器暂未解码此图片，可换用 PNG/JPEG/WebP 进行可视化编辑，或继续由服务端转换。'; views.hidden = true; controls.hidden = true; }
      };
      source.src = url;
    } else { status.textContent = '添加一张图片后，即可查看原尺寸并框选裁剪。'; views.hidden = true; controls.hidden = true; }
    return {
      refresh: schedule,
      setDisabled(value) { disabled = value; for (const input of root.querySelectorAll('input,select,button')) input.disabled = value; sourceCanvas.setAttribute('aria-disabled', String(value)); },
      destroy() { disposed = true; if (frame !== null) cancelAnimationFrame(frame); if (url) URL.revokeObjectURL(url); if (source) { source.onload = null; source.onerror = null; } root.remove(); }
    };
  }
  return { mount, dimensions };
})();
