/**
 * 通用弹窗容器（单例）。
 *
 * 打开时动态创建「遮罩 + 对话框」，支持：
 * - ESC 键关闭、点击遮罩关闭、右上角关闭、取消按钮关闭；
 * - 确认按钮支持**异步回调**与 loading 态；回调返回 `false` 时保持弹窗打开（用于表单校验失败）；
 * - `confirmModal()` 便捷二次确认（对应 PRD R-04 删除二次确认）。
 */

import { escapeHtml } from '../format.js';

/** @type {HTMLElement|null} */
let overlayEl = null;
/** @type {((e: KeyboardEvent) => void)|null} */
let keyHandler = null;

/**
 * 关闭当前弹窗（幂等）。
 */
export function closeModal() {
  if (!overlayEl) return;
  overlayEl.remove();
  overlayEl = null;
  if (keyHandler) {
    document.removeEventListener('keydown', keyHandler);
    keyHandler = null;
  }
}

/**
 * 打开弹窗。
 * @param {{
 *   title?: string,
 *   body?: string|HTMLElement,
 *   confirmText?: string,
 *   cancelText?: string,
 *   onConfirm?: (() => any) | null,
 *   onCancel?: (() => void) | null,
 *   showFooter?: boolean,
 * }} config
 * @returns {{close: () => void, dialog: HTMLElement, bodyEl: HTMLElement, confirmBtn: HTMLButtonElement|null}}
 */
export function openModal(config = {}) {
  const {
    title = '',
    body = '',
    confirmText = '保存',
    cancelText = '取消',
    onConfirm = null,
    onCancel = null,
    showFooter = true,
  } = config;

  closeModal();

  overlayEl = document.createElement('div');
  overlayEl.className = 'modal-overlay';

  const dialog = document.createElement('div');
  dialog.className = 'modal';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');

  const header = document.createElement('div');
  header.className = 'modal-header';
  header.innerHTML = `<h3 class="modal-title">${escapeHtml(title)}</h3>
    <button type="button" class="modal-close" aria-label="关闭">×</button>`;

  const bodyEl = document.createElement('div');
  bodyEl.className = 'modal-body';
  if (typeof body === 'string') {
    bodyEl.innerHTML = body;
  } else if (body) {
    bodyEl.appendChild(body);
  }

  dialog.appendChild(header);
  dialog.appendChild(bodyEl);

  /** @type {HTMLButtonElement|null} */
  let confirmBtn = null;
  /** @type {HTMLButtonElement|null} */
  let cancelBtn = null;
  if (showFooter) {
    const footer = document.createElement('div');
    footer.className = 'modal-footer';
    cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn ghost';
    cancelBtn.textContent = cancelText;
    confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn primary';
    confirmBtn.textContent = confirmText;
    footer.appendChild(cancelBtn);
    footer.appendChild(confirmBtn);
    dialog.appendChild(footer);
  }

  overlayEl.appendChild(dialog);
  document.body.appendChild(overlayEl);

  const requestClose = () => {
    if (typeof onCancel === 'function') onCancel();
    closeModal();
  };

  header.querySelector('.modal-close').addEventListener('click', requestClose);
  if (cancelBtn) cancelBtn.addEventListener('click', requestClose);
  overlayEl.addEventListener('mousedown', (event) => {
    if (event.target === overlayEl) requestClose();
  });

  if (confirmBtn && typeof onConfirm === 'function') {
    confirmBtn.addEventListener('click', async () => {
      const original = confirmBtn.textContent;
      confirmBtn.disabled = true;
      confirmBtn.textContent = '处理中…';
      try {
        const result = await onConfirm();
        if (result === false) {
          confirmBtn.disabled = false;
          confirmBtn.textContent = original;
        } else {
          closeModal();
        }
      } catch (error) {
        // 具体错误提示由调用方在 onConfirm 内处理，这里仅恢复按钮状态
        confirmBtn.disabled = false;
        confirmBtn.textContent = original;
      }
    });
  }

  keyHandler = (event) => {
    if (event.key === 'Escape') requestClose();
  };
  document.addEventListener('keydown', keyHandler);

  const focusTarget = dialog.querySelector('input, select, textarea');
  if (focusTarget) focusTarget.focus();

  return { close: closeModal, dialog, bodyEl, confirmBtn };
}

/**
 * 便捷二次确认弹窗。
 * @param {string} message 提示文案
 * @param {() => any} onConfirm 确认回调（可异步；返回 false 保持打开）
 * @param {{title?: string, confirmText?: string, cancelText?: string}} [options]
 */
export function confirmModal(message, onConfirm, options = {}) {
  return openModal({
    title: options.title || '请确认',
    body: `<p class="confirm-text">${escapeHtml(message)}</p>`,
    confirmText: options.confirmText || '确定',
    cancelText: options.cancelText || '取消',
    onConfirm,
  });
}
