let workspaces = [];
let activeWorkspace = null;
let detail = null;
let selectedFiles = [];
let cliRunning = false;
let mindmapView = { x: 40, y: 40, scale: 1 };
let mindmapDrag = null;
let mindmapFrame = null;

const supportedFileAccept =
  ".md,.markdown,.txt,.canvas,.base,.avif,.bmp,.gif,.jpeg,.jpg,.png,.svg,.webp,.pdf,.flac,.m4a,.mp3,.ogg,.wav,.webm,.3gp,.mkv,.mov,.mp4,.ogv";

const sourceKindLabels = {
  note: "笔记",
  image: "图片",
  pdf: "PDF",
  audio: "音频",
  video: "视频",
  "canvas-base": "Canvas / Bases",
  attachment: "附件",
  source: "资料",
};

const pageTypeLabels = {
  "course-overview": "课程入口",
  "learning-path": "学习路线",
  "source-map": "来源地图",
  theme: "主题页",
  case: "案例页",
  method: "方法页",
  concept: "概念页",
  "source-attachment": "来源附件页",
  question: "问题页",
  synthesis: "综合页",
  chapter: "章节页",
};

function qs(selector) {
  return document.querySelector(selector);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(formatUserError(data.error || `请求失败：${response.status}`));
  return data;
}

function showMessage(text, kind = "ok") {
  const box = qs("#message");
  box.hidden = false;
  box.textContent = formatUserError(text);
  box.className = `message ${kind}`;
  setTimeout(() => {
    box.hidden = true;
  }, kind === "error" ? 7000 : 4200);
}

function formatUserError(value) {
  const text = String(value || "");
  if (text.includes("Service temporarily unavailable") || text.includes("503")) {
    return "Claude Code CLI 执行失败：模型服务暂时不可用，请稍后重试。";
  }
  if (text.includes("API Key") || text.includes("401") || text.includes("鉴权")) {
    return "Claude Code CLI 执行失败：模型鉴权失败，请检查 API Key。";
  }
  if (text.toLowerCase().includes("model") && text.toLowerCase().includes("not found")) {
    return "Claude Code CLI 执行失败：模型名称不可用，请检查模型配置。";
  }
  return text;
}

async function loadWorkspaces() {
  await loadSettings();
  workspaces = await api("/api/workspaces");
  renderWorkspaceList();
  if (!activeWorkspace && workspaces[0]) activeWorkspace = workspaces[0].id;
  if (activeWorkspace) await loadDetail();
  if (!activeWorkspace) {
    detail = null;
    resetMindmapView();
    renderEmptyWorkspace();
  }
}

async function loadSettings() {
  try {
    const settings = await api("/api/settings");
    qs("#workspace-root").value = settings.workspaceRoot || "";
  } catch (error) {
    qs("#workspace-root").placeholder = "请重启后端以启用工作目录设置";
    showMessage("当前后端还未启用工作目录接口，请重启 start.ps1 后再保存目录。", "warn");
  }
}

async function loadDetail() {
  detail = await api(`/api/workspaces/${activeWorkspace}`);
  renderAll();
}

function renderWorkspaceList() {
  const list = qs("#workspace-list");
  if (!list) return;
  list.innerHTML =
    workspaces.length === 0
      ? `<div class="empty compact-empty">暂无课程。</div>`
      : workspaces
          .map(
            (workspace) => `
              <button class="workspace-item ${activeWorkspace === workspace.id ? "active" : ""}" data-id="${workspace.id}" type="button">
                <span class="workspace-copy">
                  <strong>${escapeHtml(workspace.name)}</strong>
                  <small>${formatDate(workspace.updatedAt || workspace.createdAt)}</small>
                </span>
                <span class="workspace-delete" data-delete-workspace="${workspace.id}" title="删除课程">删除</span>
              </button>
            `,
          )
          .join("");

  document.querySelectorAll(".workspace-item").forEach((button) => {
    button.addEventListener("click", async () => {
      activeWorkspace = button.dataset.id;
      resetMindmapView();
      renderWorkspaceList();
      await loadDetail();
    });
  });
  document.querySelectorAll("[data-delete-workspace]").forEach((button) => {
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const workspace = workspaces.find((item) => item.id === button.dataset.deleteWorkspace);
      await deleteWorkspace(button.dataset.deleteWorkspace, workspace?.name || "当前课程");
    });
  });
}

async function deleteWorkspace(workspaceId, workspaceName) {
  const confirmed = window.confirm(`确定删除课程「${workspaceName}」吗？\n\n这会同时删除该课程目录中的原始资料、Wiki 和复习资料。`);
  if (!confirmed) return;
  await deleteWorkspaceRequest(workspaceId);
  if (activeWorkspace === workspaceId) {
    activeWorkspace = null;
    resetMindmapView();
    detail = null;
  }
  showMessage("课程已删除。");
  await loadWorkspaces();
}

async function deleteWorkspaceRequest(workspaceId) {
  try {
    return await api(`/api/workspaces/${workspaceId}/delete`, { method: "POST" });
  } catch (error) {
    const fallback = await fetch(`http://127.0.0.1:8878/api/workspaces/${workspaceId}/delete`, {
      method: "POST",
    });
    const data = await fallback.json().catch(() => ({}));
    if (!fallback.ok) throw new Error(data.error || error.message);
    return data;
  }
}

function renderAll() {
  if (!detail) return;
  qs("#active-title").textContent = detail.name;
  qs("#metric-sources").textContent = String(detail.sources.length);
  qs("#metric-pages").textContent = String(detail.pages.filter((page) => page.type !== "scaffold-index" && page.status !== "scaffold").length);
  qs("#metric-issues").textContent = String(detail.issues.length);
  qs("#metric-runs").textContent = String(detail.runs.length);
  renderIssues();
  renderSources();
  renderWikiMindmap();
  renderCliConsole();
}

function renderEmptyWorkspace() {
  qs("#active-title").textContent = "请先创建课程";
  qs("#metric-sources").textContent = "0";
  qs("#metric-pages").textContent = "0";
  qs("#metric-issues").textContent = "0";
  qs("#metric-runs").textContent = "0";
  qs("#issue-list").innerHTML = `<div class="empty">暂无待处理问题。</div>`;
  renderEmptyMindmap("请先创建课程");
  renderCliConsole();
}

function renderIssues() {
  qs("#issue-list").innerHTML =
    detail.issues.length === 0
      ? `<div class="empty">暂无待处理问题。</div>`
      : detail.issues
          .map((issue) => `<div class="issue ${issue.severity || "medium"}">${escapeHtml(issue.message)}</div>`)
          .join("");
}

function renderSources() {
  // 原始资料页只负责上传，不再展示来源列表。
}

function renderWikiMindmap() {
  const box = qs("#wiki-mindmap");
  if (!box) return;
  const wikiPages = detail.pages || [];
  if (!wikiPages.length) {
    renderEmptyMindmap("初始化 Wiki 后，这里会显示 Wiki 骨架、正文文件与来源关系。");
    return;
  }

  const contentPageCount = wikiPages.filter((page) => !isScaffoldPage(page)).length;
  const pageGroups = groupPagesForMindmap(wikiPages);
  const width = 2200;
  const height = Math.max(860, pageGroups.length * 210 + 220);
  const rootX = 260;
  const rootY = Math.max(220, height / 2);
  const pageX = 760;
  const sourceX = 1320;
  const groupStep = 210;
  const firstY = rootY - ((pageGroups.length - 1) * groupStep) / 2;
  const pageNodes = pageGroups.map((page, index) => {
    const isScaffold = isScaffoldPage(page);
    const pageY = firstY + index * groupStep;
    const allRefs = normalizeSourceRefs(page);
    const refs = allRefs.length > 4 ? allRefs.slice(0, 3) : allRefs.slice(0, 4);
    const sourceNodeCount = allRefs.length > refs.length ? refs.length + 1 : refs.length;
    const refNodes = refs.map((ref, refIndex) => {
      const refY = pageY - ((sourceNodeCount - 1) * 46) / 2 + refIndex * 46;
      return `
        <path class="mindmap-link source-link" d="M ${pageX + 300} ${pageY} C ${pageX + 430} ${pageY}, ${sourceX - 150} ${refY}, ${sourceX} ${refY}" />
        <g class="mindmap-node source-node ${sourcePathFromRef(ref) ? "openable-node" : ""}" ${sourcePathFromRef(ref) ? `data-open-path="${escapeHtml(sourcePathFromRef(ref))}" tabindex="0" role="button"` : `tabindex="0"`}>
          <rect x="${sourceX}" y="${refY - 20}" width="500" height="40" rx="8"></rect>
          <text x="${sourceX + 16}" y="${refY + 5}">${escapeSvg(clipText(formatSourceRef(ref), 58))}</text>
        </g>
      `;
    }).join("");
    const moreRefY = pageY - ((sourceNodeCount - 1) * 46) / 2 + refs.length * 46;
    const moreRefsNode = allRefs.length > refs.length
      ? `
        <path class="mindmap-link source-link" d="M ${pageX + 300} ${pageY} C ${pageX + 430} ${pageY}, ${sourceX - 150} ${moreRefY}, ${sourceX} ${moreRefY}" />
        <g class="mindmap-node source-node muted-node">
          <rect x="${sourceX}" y="${moreRefY - 20}" width="300" height="40" rx="8"></rect>
          <text x="${sourceX + 16}" y="${moreRefY + 5}">另有 ${allRefs.length - refs.length} 个来源</text>
        </g>
      `
      : "";
    return `
      <path class="mindmap-link page-link" d="M ${rootX + 260} ${rootY} C ${rootX + 420} ${rootY}, ${pageX - 180} ${pageY}, ${pageX} ${pageY}" />
      <g class="mindmap-node page-node openable-node ${isScaffold ? "scaffold-node muted-node" : ""}" data-open-path="${escapeHtml(page.path)}" tabindex="0" role="button">
        <rect x="${pageX}" y="${pageY - 42}" width="300" height="84" rx="10"></rect>
        <text class="node-title" x="${pageX + 18}" y="${pageY - 10}">${escapeSvg(clipText(fileNameFromPath(page.path), 26))}</text>
        <text class="node-meta" x="${pageX + 18}" y="${pageY + 14}">${escapeSvg(isScaffold ? "骨架页" : labelPageType(page.type))} / ${(page.sourceRefs || page.sourceIds || []).length} 个来源</text>
      </g>
      ${refNodes || `
        <path class="mindmap-link source-link" d="M ${pageX + 300} ${pageY} C ${pageX + 430} ${pageY}, ${sourceX - 150} ${pageY}, ${sourceX} ${pageY}" />
        <g class="mindmap-node source-node muted-node">
          <rect x="${sourceX}" y="${pageY - 20}" width="300" height="40" rx="8"></rect>
          <text x="${sourceX + 16}" y="${pageY + 5}">${isScaffold ? "等待生成正文与来源" : "暂无来源章节"}</text>
        </g>
      `}
      ${moreRefsNode}
    `;
  }).join("");

  box.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Wiki 文件与原始资料章节思维导图">
      <g id="mindmap-stage" transform="translate(${mindmapView.x} ${mindmapView.y}) scale(${mindmapView.scale})">
        <text class="mindmap-label" x="${rootX}" y="${rootY - 90}">课程</text>
        <text class="mindmap-label" x="${pageX}" y="${Math.max(60, firstY - 78)}">Wiki 文件</text>
        <text class="mindmap-label" x="${sourceX}" y="${Math.max(60, firstY - 78)}">原始资料章节</text>
        <g class="mindmap-node root-node">
          <rect x="${rootX}" y="${rootY - 54}" width="260" height="108" rx="14"></rect>
          <text class="node-title" x="${rootX + 20}" y="${rootY - 10}">${escapeSvg(clipText(detail.name || "课程知识库", 18))}</text>
          <text class="node-meta" x="${rootX + 20}" y="${rootY + 18}">${contentPageCount ? `${contentPageCount} 个正文节点` : "已生成 Wiki 骨架"} / ${detail.sources.length} 份原始资料</text>
        </g>
        ${pageNodes}
      </g>
    </svg>
  `;
  bindMindmapInteractions();
}

function normalizeSourceRefs(page) {
  if (page.sourceRefs && page.sourceRefs.length) return page.sourceRefs;
  return (page.sourceIds || []).map((sourceId) => {
    const source = (detail.sources || []).find((item) => item.id === sourceId);
    return {
      fileName: source?.fileName || sourceId,
      storedPath: source?.storedPath || "",
      section: "原始文件",
    };
  });
}

function formatSourceRef(ref) {
  const file = ref.storedPath || ref.fileName || "原始资料";
  const section = ref.section || "原始文件";
  const range = ref.lineStart && ref.lineEnd ? ` 行 ${ref.lineStart}-${ref.lineEnd}` : "";
  return `${file} / ${section}${range}`;
}

function sourcePathFromRef(ref) {
  const source = ref.sourceId ? (detail.sources || []).find((item) => item.id === ref.sourceId) : null;
  const path = ref.storedPath || source?.storedPath || "";
  if (!path) return "";
  return path.startsWith("原始资料/") ? path : `原始资料/${path}`;
}

function renderEmptyMindmap(message) {
  const box = qs("#wiki-mindmap");
  if (!box) return;
  box.innerHTML = `<div class="empty mindmap-empty">${escapeHtml(message)}</div>`;
}

function groupPagesForMindmap(pages) {
  return [...pages].sort((left, right) => {
    const typeOrder = ["course-overview", "learning-path", "source-map", "theme", "concept", "method", "case", "question", "source-attachment"];
    const leftIndex = typeOrder.indexOf(left.type);
    const rightIndex = typeOrder.indexOf(right.type);
    const leftRank = leftIndex === -1 ? typeOrder.length : leftIndex;
    const rightRank = rightIndex === -1 ? typeOrder.length : rightIndex;
    if (leftRank !== rightRank) return leftRank - rightRank;
    return String(left.path || left.title || "").localeCompare(String(right.path || right.title || ""), "zh-CN");
  });
}

function isScaffoldPage(page) {
  return page?.type === "scaffold-index" || page?.status === "scaffold";
}

function bindMindmapInteractions() {
  const box = qs("#wiki-mindmap");
  const svg = box?.querySelector("svg");
  if (!box || !svg) return;

  svg.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    svg.setPointerCapture(event.pointerId);
    const startPoint = getMindmapPointerPoint(svg, event);
    mindmapDrag = {
      pointerId: event.pointerId,
      startX: startPoint.x,
      startY: startPoint.y,
      startClientX: event.clientX,
      startClientY: event.clientY,
      viewX: mindmapView.x,
      viewY: mindmapView.y,
      targetNode: event.target.closest?.("[data-open-path]") || null,
      moved: false,
    };
    box.classList.add("dragging");
  });

  svg.addEventListener("pointermove", (event) => {
    if (!mindmapDrag || mindmapDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    const point = getMindmapPointerPoint(svg, event);
    mindmapView.x = mindmapDrag.viewX + point.x - mindmapDrag.startX;
    mindmapView.y = mindmapDrag.viewY + point.y - mindmapDrag.startY;
    const movedX = event.clientX - mindmapDrag.startClientX;
    const movedY = event.clientY - mindmapDrag.startClientY;
    if (Math.hypot(movedX, movedY) > 5) mindmapDrag.moved = true;
    scheduleMindmapTransform();
  });

  svg.addEventListener("pointerup", endMindmapDrag);
  svg.addEventListener("pointercancel", endMindmapDrag);
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoomMindmap(event.deltaY > 0 ? 0.9 : 1.1);
  }, { passive: false });
  svg.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const node = event.target.closest?.("[data-open-path]");
    if (!node) return;
    event.preventDefault();
    openMindmapNode(node.dataset.openPath);
  });
}

function endMindmapDrag(event) {
  const box = qs("#wiki-mindmap");
  const svg = box?.querySelector("svg");
  const drag = mindmapDrag;
  if (svg && mindmapDrag?.pointerId === event.pointerId && (!svg.hasPointerCapture || svg.hasPointerCapture(event.pointerId))) {
    svg.releasePointerCapture(event.pointerId);
  }
  mindmapDrag = null;
  box?.classList.remove("dragging");
  if (drag?.pointerId === event.pointerId && drag.targetNode && !drag.moved) {
    openMindmapNode(drag.targetNode.dataset.openPath);
  }
}

function getMindmapPointerPoint(svg, event) {
  const matrix = svg.getScreenCTM();
  if (!matrix) return { x: event.clientX, y: event.clientY };
  const point = svg.createSVGPoint();
  point.x = event.clientX;
  point.y = event.clientY;
  return point.matrixTransform(matrix.inverse());
}

function scheduleMindmapTransform() {
  if (mindmapFrame) return;
  mindmapFrame = window.requestAnimationFrame(() => {
    mindmapFrame = null;
    updateMindmapTransform();
  });
}

function updateMindmapTransform() {
  const stage = qs("#mindmap-stage");
  if (!stage) return;
  stage.setAttribute("transform", `translate(${mindmapView.x} ${mindmapView.y}) scale(${mindmapView.scale})`);
}

function zoomMindmap(factor) {
  mindmapView.scale = Math.min(1.7, Math.max(0.45, Number((mindmapView.scale * factor).toFixed(2))));
  updateMindmapTransform();
}

function resetMindmapView() {
  mindmapView = { x: 40, y: 40, scale: 1 };
  mindmapDrag = null;
  updateMindmapTransform();
}

async function openMindmapNode(relativePath) {
  if (!activeWorkspace || !relativePath) return;
  try {
    await api(`/api/workspaces/${activeWorkspace}/open-in-obsidian`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: relativePath }),
    });
    showMessage(`正在 Obsidian 中打开：${relativePath}`);
  } catch (error) {
    showMessage(error.message, "error");
  }
}

function clipText(value, maxLength) {
  const text = String(value || "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength - 1)}…`;
}

function renderCliConsole() {
  const chatThread = qs("#cli-terminal");
  const sessionBox = qs("#cli-session-id");
  const workspaceBox = qs("#cli-workspace-path");
  const status = qs("#cli-status");
  const workspaceReady = Boolean(activeWorkspace && detail);
  const consoleState = detail?.console || {};
  if (sessionBox) {
    sessionBox.textContent = consoleState.sessionId ? clipText(consoleState.sessionId, 22) : "尚未启动";
    sessionBox.title = consoleState.sessionId || "";
  }
  if (workspaceBox) {
    workspaceBox.textContent = detail?.path ? clipText(detail.path, 52) : "请选择课程";
    workspaceBox.title = detail?.path || "";
  }
  if (status) {
    status.textContent = cliRunning ? "运行中" : consoleState.sessionId ? "已连接" : "未启动";
    status.classList.toggle("running", cliRunning);
  }
  ["#cli-start-btn", "#cli-reset-btn", "#cli-send-btn"].forEach((selector) => {
    const button = qs(selector);
    if (button) button.disabled = !workspaceReady || cliRunning;
  });
  const input = qs("#cli-input");
  if (input) input.disabled = !workspaceReady || cliRunning;
  if (!chatThread) return;
  const history = consoleState.history || [];
  if (!workspaceReady) {
    chatThread.innerHTML = renderAgentNotice("请选择或创建课程，然后启动 Agent。");
    return;
  }
  if (!history.length) {
    chatThread.innerHTML = [
      renderAgentWelcome(),
      renderAgentNotice("启动后，你可以直接用自然语言要求它学习、整理 Wiki 或生成复习资料。"),
    ].join("");
    return;
  }
  chatThread.innerHTML = history
    .map((entry) => renderCliHistoryEntry(entry))
    .join("");
  chatThread.scrollTop = chatThread.scrollHeight;
}

function renderCliHistoryEntry(entry) {
  const role = entry.role === "user" ? "user" : "assistant";
  const label = role === "user" ? "你" : "Agent";
  return `
    <div class="chat-message ${role}">
      <div class="chat-avatar" aria-hidden="true">${role === "user" ? "你" : "A"}</div>
      <div class="chat-bubble">
        <div class="chat-author">${label}</div>
        <div class="chat-content">${renderMessageMarkdown(entry.text || "")}</div>
      </div>
    </div>
  `;
}

function appendCliSystemLine(text, kind = "system") {
  const chatThread = qs("#cli-terminal");
  if (!chatThread) return;
  chatThread.insertAdjacentHTML("beforeend", renderAgentNotice(text, kind));
  chatThread.scrollTop = chatThread.scrollHeight;
}

function appendChatMessage(role, text) {
  const chatThread = qs("#cli-terminal");
  if (!chatThread) return;
  chatThread.insertAdjacentHTML("beforeend", renderCliHistoryEntry({ role, text }));
  chatThread.scrollTop = chatThread.scrollHeight;
}

function renderAgentWelcome() {
  return `
    <div class="agent-welcome">
      <div>
        <span class="agent-orb">A</span>
      </div>
      <div>
        <h4>还没有开始对话</h4>
        <p>点击“启动 Agent”后，可以像和 Codex 一样直接描述目标。底层会使用 Claude Code 和 Skills 处理课程文件。</p>
      </div>
    </div>
  `;
}

function renderAgentNotice(text, kind = "system") {
  return `<div class="agent-notice ${kind}">${escapeHtml(text)}</div>`;
}

function renderMessageMarkdown(value) {
  const text = escapeHtml(String(value || "").trim());
  if (!text) return "";
  const blocks = [];
  const parts = text.split(/```/);
  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      const code = part.replace(/^[a-zA-Z0-9_-]+\n/, "");
      blocks.push(`<pre class="message-code"><code>${code}</code></pre>`);
      return;
    }
    blocks.push(renderMarkdownTextBlock(part));
  });
  return blocks.join("");
}

function renderMarkdownTextBlock(block) {
  const lines = block.split(/\n/);
  const html = [];
  let listItems = [];
  let tableRows = [];
  const flushList = () => {
    if (!listItems.length) return;
    html.push(`<ul>${listItems.map((item) => `<li>${formatInlineMarkdown(item)}</li>`).join("")}</ul>`);
    listItems = [];
  };
  const flushTable = () => {
    if (tableRows.length < 2) {
      tableRows.forEach((row) => html.push(`<p>${formatInlineMarkdown(row)}</p>`));
      tableRows = [];
      return;
    }
    const rows = tableRows
      .filter((row) => !/^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(row))
      .map((row) =>
        row
          .replace(/^\|/, "")
          .replace(/\|$/, "")
          .split("|")
          .map((cell) => cell.trim()),
      );
    const [head, ...body] = rows;
    html.push(`
      <div class="message-table-wrap">
        <table class="message-table">
          <thead><tr>${head.map((cell) => `<th>${formatInlineMarkdown(cell)}</th>`).join("")}</tr></thead>
          <tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${formatInlineMarkdown(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
        </table>
      </div>
    `);
    tableRows = [];
  };
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList();
      flushTable();
      return;
    }
    if (/^\|.+\|$/.test(trimmed)) {
      flushList();
      tableRows.push(trimmed);
      return;
    }
    flushTable();
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushList();
      const level = Math.min(heading[1].length + 2, 5);
      html.push(`<h${level}>${formatInlineMarkdown(heading[2])}</h${level}>`);
      return;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      listItems.push(bullet[1]);
      return;
    }
    flushList();
    html.push(`<p>${formatInlineMarkdown(trimmed)}</p>`);
  });
  flushList();
  flushTable();
  return html.join("");
}

function formatInlineMarkdown(value) {
  return value
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+?)`/g, "<code>$1</code>");
}

function setCliRunning(value, label = "") {
  cliRunning = value;
  const status = qs("#cli-status");
  if (status) {
    status.textContent = value ? (label || "运行中") : detail?.console?.sessionId ? "已连接" : "未启动";
    status.classList.toggle("running", value);
  }
  renderCliConsole();
}

async function startCliConsole(reset = false) {
  if (!activeWorkspace) {
    showMessage("请先创建或选择课程。", "warn");
    return;
  }
  setCliRunning(true, "启动中");
  appendCliSystemLine(reset ? "正在重置并启动 Agent..." : "正在启动 Agent...");
  try {
    const result = await api(`/api/workspaces/${activeWorkspace}/console/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reset }),
    });
    detail.console = {
      ...(detail.console || {}),
      sessionId: result.sessionId,
      history: result.history || [],
    };
    showMessage(result.ok ? "Agent 已启动。" : "Agent 返回了错误，请查看对话区。", result.ok ? "ok" : "error");
    await loadDetail();
    switchView("agent");
  } catch (error) {
    appendCliSystemLine(error.message, "error");
    showMessage(error.message, "error");
  } finally {
    setCliRunning(false);
  }
}

async function sendCliMessage(message = null) {
  if (!activeWorkspace) {
    showMessage("请先创建或选择课程。", "warn");
    return;
  }
  const input = qs("#cli-input");
  const text = (message ?? input?.value ?? "").trim();
  if (!text) {
    showMessage("请先输入要发送给 Agent 的内容。", "warn");
    return;
  }
  if (input && message === null) input.value = "";
  setCliRunning(true, "执行中");
  appendChatMessage("user", text);
  appendCliSystemLine("Agent 正在处理...", "system");
  try {
    const result = await api(`/api/workspaces/${activeWorkspace}/console/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    detail.console = {
      ...(detail.console || {}),
      sessionId: result.sessionId,
      history: result.history || [],
    };
    await loadDetail();
    switchView("agent");
  } catch (error) {
    appendCliSystemLine(error.message, "error");
    showMessage(error.message, "error");
  } finally {
    setCliRunning(false);
  }
}

async function resetCliConsole() {
  if (!activeWorkspace) return;
  const confirmed = window.confirm("确定重置当前课程的 Claude Code CLI 会话吗？历史输出会从页面中清空，但课程文件不会删除。");
  if (!confirmed) return;
  setCliRunning(true, "重置中");
  try {
    const result = await api(`/api/workspaces/${activeWorkspace}/console/reset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    detail.console = { ...(detail.console || {}), sessionId: "", history: result.history || [] };
    renderCliConsole();
    showMessage("Claude Code 会话已重置。");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    setCliRunning(false);
  }
}

async function uploadFiles() {
  if (!selectedFiles.length) {
    showMessage("请先选择文件或文件夹。", "warn");
    return;
  }
  const form = new FormData();
  selectedFiles.forEach((file) => {
    const relativePath = file.webkitRelativePath || file.name;
    form.append("files", file, relativePath);
    form.append("relativePaths", relativePath);
  });
  const result = await api(`/api/workspaces/${activeWorkspace}/upload`, {
    method: "POST",
    body: form,
  });
  selectedFiles = [];
  qs("#file-input").value = "";
  qs("#folder-input").value = "";
  updateSelectedSourceSummary();
  showMessage(`已复制 ${result.uploaded} 个文件到课程目录。`);
  await loadDetail();
}

function updateSelectedSourceSummary() {
  const summary = qs("#selected-source-summary");
  if (!summary) return;
  if (!selectedFiles.length) {
    summary.textContent = "尚未选择文件。";
    return;
  }
  const totalSize = selectedFiles.reduce((sum, file) => sum + (file.size || 0), 0);
  const hasFolder = selectedFiles.some((file) => file.webkitRelativePath);
  summary.textContent = `${hasFolder ? "已选择文件夹" : "已选择文件"}：${selectedFiles.length} 个文件，${formatFileSize(totalSize)}`;
}

function switchView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  qs(`#${name}`).classList.add("active");
  document.querySelector(`.nav-item[data-view="${name}"]`).classList.add("active");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});

document.querySelectorAll("[data-collapsible] .section-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    const section = button.closest("[data-collapsible]");
    const collapsed = section.classList.toggle("collapsed");
    button.setAttribute("aria-expanded", String(!collapsed));
  });
});

qs("#create-workspace-btn").addEventListener("click", async () => {
  const name = qs("#workspace-name").value.trim() || "未命名课程";
  const workspace = await api("/api/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  qs("#workspace-name").value = "";
  activeWorkspace = workspace.id;
  resetMindmapView();
  showMessage("课程已创建。");
  await loadWorkspaces();
});

qs("#save-root-btn").addEventListener("click", async () => {
  const workspaceRoot = qs("#workspace-root").value.trim();
  const settings = await api("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspaceRoot }),
  });
  qs("#workspace-root").value = settings.workspaceRoot || "";
  showMessage("工作目录已保存；如果目录不存在，系统会自动新建。之后新建的课程会写入该目录。");
});

qs("#file-input").addEventListener("change", (event) => {
  selectedFiles = [...event.target.files];
  qs("#folder-input").value = "";
  updateSelectedSourceSummary();
  showMessage(`已选择 ${selectedFiles.length} 个文件。`);
});

qs("#folder-input").addEventListener("change", (event) => {
  selectedFiles = [...event.target.files];
  qs("#file-input").value = "";
  updateSelectedSourceSummary();
  showMessage(`已选择文件夹内 ${selectedFiles.length} 个文件。`);
});

qs("#pick-files-btn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  qs("#file-input").click();
});

qs("#pick-folder-btn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  qs("#folder-input").click();
});

qs("#upload-btn").addEventListener("click", uploadFiles);
qs("#mindmap-zoom-out")?.addEventListener("click", () => zoomMindmap(0.9));
qs("#mindmap-zoom-in")?.addEventListener("click", () => zoomMindmap(1.1));
qs("#mindmap-reset")?.addEventListener("click", resetMindmapView);
qs("#cli-start-btn")?.addEventListener("click", () => startCliConsole(false));
qs("#cli-reset-btn")?.addEventListener("click", resetCliConsole);
qs("#cli-send-btn")?.addEventListener("click", () => sendCliMessage());
qs("#cli-input")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    sendCliMessage();
  }
});

loadWorkspaces().catch((error) => showMessage(error.message, "error"));

function labelSourceKind(kind) {
  return sourceKindLabels[kind] || kind || "资料";
}

function labelPageType(type) {
  return pageTypeLabels[type] || type || "-";
}

function fileNameFromPath(path) {
  return String(path || "").split("/").pop() || path || "-";
}

function escapeSvg(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatDate(value) {
  if (!value) return "未记录时间";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "未记录时间";
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}

function formatFileSize(value) {
  if (!Number.isFinite(value)) return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
