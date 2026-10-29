/**
 * 列表页「电子保单」只读选择器（v1.3）。
 *
 * 按保单附件数分三种行为（见 ARCHITECTURE-v13-increment §4.4 / D10）：
 * - ``N == 0``：toast「该保单暂无电子保单」；
 * - ``N == 1``：直接 ``window.open`` 既有 ``inline`` 查看接口（新标签预览）；
 * - ``N >= 2``：复用 ``modal.js`` 弹出选择层（点文件名查看 / 每项「下载」）。
 *
 * 附件 id 来源：列表响应仅含 ``attachmentCount``（沿用「不返回附件明细」决策），
 * 故点击时**按需** ``GET /api/policies/{id}/attachments``。
 *
 * 零外部依赖：复用既有 ``api.js`` / ``modal.js`` / ``format.js``。
 */

import * as api from '../api.js';
import * as store from '../store.js';
import { escapeHtml, fmtFileSize, fmtDate } from '../format.js';
import { openModal } from './modal.js';

/**
 * 打开「电子保单」选择 / 直达。
 * @param {object} policy 保单（至少含 id / company / productName / attachmentCount）
 */
export async function openAttachmentPicker(policy) {
  const count = Number(policy && policy.attachmentCount) || 0;

  // N == 0：无附件
  if (count === 0) {
    store.notify('该保单暂无电子保单', 'info');
    return;
  }

  let attachments = [];
  try {
    const data = await api.getAttachments(policy.id);
    attachments = (data && data.attachments) || [];
  } catch (error) {
    store.notify(error.message || '获取电子保单失败', 'error');
    return;
  }

  // 计数与实际不符时（如刚被删除），按实际为准
  if (attachments.length === 0) {
    store.notify('该保单暂无电子保单', 'info');
    return;
  }

  // N == 1：直接新标签打开
  if (attachments.length === 1) {
    const url = api.attachmentContentUrl(policy.id, attachments[0].id, 'inline');
    window.open(url, '_blank', 'noopener');
    return;
  }

  // N >= 2：弹选择层
  openAttachmentPickerModal(policy, attachments);
}

/**
 * 弹出「选择要查看的电子保单」选择层。
 * @param {object} policy
 * @param {Array} attachments
 */
function openAttachmentPickerModal(policy, attachments) {
  const rows = attachments
    .map((att) => {
      const viewUrl = api.attachmentContentUrl(policy.id, att.id, 'inline');
      const downloadUrl = api.attachmentContentUrl(policy.id, att.id, 'attachment');
      return `<div class="attachment-item">
        <div class="attachment-meta">
          <a class="attachment-link" href="${escapeHtml(viewUrl)}" target="_blank" rel="noopener">${escapeHtml(att.fileName)}</a>
          <span class="attachment-sub">${escapeHtml(fmtFileSize(att.fileSize))} · ${escapeHtml(fmtDate(att.uploadedAt))}</span>
        </div>
        <div class="attachment-actions">
          <a class="btn small" href="${escapeHtml(downloadUrl)}">下载</a>
        </div>
      </div>`;
    })
    .join('');

  openModal({
    title: `电子保单 · ${policy.company} · ${policy.productName}`,
    body: `<div class="attachment-items">${rows}</div>
      <p class="attachment-note">点击文件名在新标签页查看；点「下载」保存到本地。</p>`,
    showFooter: false,
  });
}

export default openAttachmentPicker;
