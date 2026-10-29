/**
 * 电子保单附件组件（v1.2 / T9）。
 *
 * `createAttachmentList(policyId, { onCountChange })` → `{ element, reload }`：
 * - **上传**：`<input type="file" multiple accept=".pdf">`（隐藏，由按钮触发）；前端仅做
 *   「是否 PDF」的友好预校验，**真正的安全校验在服务端**（magic bytes + 流式大小，见 6.9）；
 * - **列表**：文件名 / 大小（`fmtFileSize`）/ 上传时间（`fmtDate`）+ 查看 / 下载 / 删除；
 * - **查看** = `inline` 新标签打开；**下载** = `attachment` 触发浏览器下载
 *   （URL 均由 `api.attachmentContentUrl` 生成，**不走 fetch**）；
 * - **删除**走**内联二次确认**——不新开弹窗，避免与所在「编辑弹窗」的单例 `modal.js` 冲突；
 * - 每次刷新后回调 `onCountChange(n)`，供调用方同步计数（如列表删除确认文案）。
 *
 * 仅依赖既有 `api.js` / `format.js` / `store.js`，零第三方依赖。
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { escapeHtml, fmtFileSize, fmtDate } from '../format.js';

/**
 * 判断文件是否可能是 PDF（前端友好预校验；服务端仍会校验 magic bytes）。
 * @param {File} file
 * @returns {boolean}
 */
function isPdf(file) {
  if (!file) return false;
  const name = file.name ? String(file.name).toLowerCase() : '';
  return name.endsWith('.pdf') || file.type === 'application/pdf';
}

/**
 * 创建附件区组件。
 * @param {string} policyId 所属保单 id
 * @param {{onCountChange?: (n: number) => void}} [options]
 * @returns {{element: HTMLElement, reload: () => Promise<void>}}
 */
export function createAttachmentList(policyId, options = {}) {
  const { onCountChange } = options;

  const wrap = document.createElement('div');
  wrap.className = 'attachment-list';
  wrap.innerHTML = `
    <div class="attachment-head">
      <button type="button" class="btn small primary" data-role="upload">+ 上传 PDF</button>
      <span class="attachment-count" data-role="count">已挂载 0 个附件</span>
      <input type="file" accept="application/pdf,.pdf" multiple hidden data-role="file">
    </div>
    <div class="attachment-items" data-role="items"><div class="attachment-empty">加载中…</div></div>
    <p class="attachment-note">仅支持 PDF 文件；上传后可在列表中查看 / 下载 / 删除。</p>
  `;

  const uploadBtn = wrap.querySelector('[data-role="upload"]');
  const fileInput = wrap.querySelector('[data-role="file"]');
  const countEl = wrap.querySelector('[data-role="count"]');
  const itemsEl = wrap.querySelector('[data-role="items"]');

  /**
   * 更新计数文案并回调。
   * @param {number} n
   */
  function notifyCount(n) {
    const safe = Number(n) || 0;
    countEl.textContent = `已挂载 ${safe} 个附件`;
    if (typeof onCountChange === 'function') onCountChange(safe);
  }

  /**
   * 单个附件条目 HTML（含「操作」与「二次确认」两组按钮）。
   * @param {object} att 附件（camelCase：id / fileName / fileSize / contentType / uploadedAt）
   * @returns {string}
   */
  function itemHtml(att) {
    const name = att && att.fileName != null ? String(att.fileName) : '（未命名）';
    return `<div class="attachment-item" data-id="${escapeHtml(att.id)}">
      <div class="attachment-meta">
        <span class="attachment-name" title="${escapeHtml(name)}">📄 ${escapeHtml(name)}</span>
        <span class="attachment-sub">${escapeHtml(fmtFileSize(att.fileSize))} · ${escapeHtml(fmtDate(att.uploadedAt))} 上传</span>
      </div>
      <div class="attachment-actions" data-role="actions">
        <button type="button" class="btn small" data-act="view">查看</button>
        <button type="button" class="btn small" data-act="download">下载</button>
        <button type="button" class="btn small danger" data-act="delete">删除</button>
      </div>
      <div class="attachment-actions" data-role="confirm" hidden>
        <span class="attachment-confirm-text">确认删除？</span>
        <button type="button" class="btn small danger" data-act="confirm-del">确认</button>
        <button type="button" class="btn small" data-act="cancel-del">取消</button>
      </div>
    </div>`;
  }

  /**
   * 渲染附件列表。
   * @param {object} data 列表接口响应（{ policyId, attachmentCount, attachments }）
   */
  function renderList(data) {
    const list = (data && data.attachments) || [];
    if (!list.length) {
      itemsEl.innerHTML = '<div class="attachment-empty">暂无附件</div>';
      return;
    }
    itemsEl.innerHTML = list.map(itemHtml).join('');
  }

  /**
   * 切换某条目的「操作 / 二次确认」两组按钮。
   * @param {HTMLElement} item
   * @param {boolean} show
   */
  function toggleConfirm(item, show) {
    if (!item) return;
    const actions = item.querySelector('[data-role="actions"]');
    const confirm = item.querySelector('[data-role="confirm"]');
    if (actions) actions.hidden = show;
    if (confirm) confirm.hidden = !show;
  }

  /**
   * 触发浏览器下载（服务端已设置 `Content-Disposition: attachment`）。
   * @param {string} url
   */
  function triggerDownload(url) {
    const a = document.createElement('a');
    a.href = url;
    a.rel = 'noopener';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  /** 加载（或重新加载）附件列表。 */
  async function reload() {
    itemsEl.innerHTML = '<div class="attachment-empty">加载中…</div>';
    let data;
    try {
      data = await api.getAttachments(policyId);
    } catch (error) {
      itemsEl.innerHTML = `<div class="attachment-empty error">附件加载失败：${escapeHtml(error.message)}</div>`;
      return;
    }
    renderList(data);
    notifyCount(data ? data.attachmentCount : 0);
  }

  /**
   * 上传所选文件（前端预校验 PDF，服务端二次强校验）。
   * @param {File[]} files
   */
  async function doUpload(files) {
    if (!files.length) return;
    if (!files.every(isPdf)) {
      store.notify('仅支持 PDF 文件', 'error');
      return;
    }
    uploadBtn.disabled = true;
    const original = uploadBtn.textContent;
    uploadBtn.textContent = '上传中…';
    try {
      const result = await api.uploadAttachments(policyId, files);
      const n = result && result.uploaded ? result.uploaded.length : files.length;
      store.notify(`已上传 ${n} 个附件`, 'success');
      await reload();
    } catch (error) {
      store.notify(error.message, 'error');
    } finally {
      uploadBtn.disabled = false;
      uploadBtn.textContent = original;
    }
  }

  /**
   * 删除单个附件（先删 DB 行，再 best-effort 删文件由服务端保证）。
   * @param {string} attId
   * @param {HTMLElement} item
   */
  async function doDelete(attId, item) {
    try {
      await api.deleteAttachment(policyId, attId);
      store.notify('附件已删除', 'success');
      await reload();
    } catch (error) {
      store.notify(error.message, 'error');
      toggleConfirm(item, false);
    }
  }

  // ---- 事件绑定（itemsEl 用委托，重渲染后无需重新绑定） ----
  uploadBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async () => {
    const files = Array.from(fileInput.files || []);
    fileInput.value = ''; // 允许再次选择同名文件
    await doUpload(files);
  });

  itemsEl.addEventListener('click', (event) => {
    const btn = event.target.closest('button[data-act]');
    if (!btn) return;
    const item = btn.closest('.attachment-item');
    if (!item) return;
    const id = item.dataset.id;
    const act = btn.dataset.act;
    if (act === 'view') {
      window.open(api.attachmentContentUrl(policyId, id, 'inline'), '_blank', 'noopener');
    } else if (act === 'download') {
      triggerDownload(api.attachmentContentUrl(policyId, id, 'attachment'));
    } else if (act === 'delete') {
      toggleConfirm(item, true);
    } else if (act === 'cancel-del') {
      toggleConfirm(item, false);
    } else if (act === 'confirm-del') {
      doDelete(id, item);
    }
  });

  // 首次加载
  reload();

  return { element: wrap, reload };
}
