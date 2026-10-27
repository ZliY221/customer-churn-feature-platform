const $ = selector => document.querySelector(selector)
const state = { priority: [], tasks: [], outcomes: new Map(), selectedTasks: new Set(), eventCursor: 0, polling: false, loadPromise: null, reloadQueued: false, pollTimer: null }
const bandNames = { CRITICAL: '极高风险', HIGH: '高风险', WATCH: '关注', STABLE: '稳定' }
const statusNames = { OPEN: '待执行', IN_PROGRESS: '处理中', COMPLETED: '已完成', DISMISSED: '已关闭' }
const workspacePages = {
  overview: ['客户留存运营工作台', '先看组合健康度，再进入需要处理的客户与任务'],
  priority: ['高风险客户队列', '按流失概率和风险等级安排当天的联系顺序'],
  tasks: ['留存协同任务', '集中完成负责人分派、批量推进和任务交接'],
  outcomes: ['留存效果复盘', '对照联系结果与成熟观察窗口复核策略效果'],
}

function activateWorkspacePage(page, updateHash = true) {
  const selected = workspacePages[page] ? page : 'overview'
  document.body.dataset.workspacePage = selected
  document.querySelectorAll('[data-workspace-page-view]').forEach(view => {
    view.hidden = view.dataset.workspacePageView !== selected
  })
  document.querySelectorAll('[data-workspace-link]').forEach(link => {
    const active = link.dataset.workspaceLink === selected
    link.classList.toggle('active', active)
    link.setAttribute('aria-current', active ? 'page' : 'false')
  })
  $('#workspace-title').textContent = workspacePages[selected][0]
  $('#workspace-subtitle').textContent = workspacePages[selected][1]
  if (updateHash) history.replaceState(null, '', `#${selected}`)
  try { localStorage.setItem('churn-workspace-page', selected) } catch (_) {}
  if (updateHash) $('#main-content').focus()
}

const workspaceLinks = [...document.querySelectorAll('[data-workspace-link]')]
workspaceLinks.forEach((link, index) => {
  link.addEventListener('click', event => {
    event.preventDefault()
    activateWorkspacePage(link.dataset.workspaceLink)
  })
  link.addEventListener('keydown', event => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? workspaceLinks.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + workspaceLinks.length) % workspaceLinks.length
    workspaceLinks[next].focus()
  })
})

async function request(path, options) {
  const response = await fetch(path, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `请求失败：${response.status}`)
  }
  return response.json()
}

function percentage(value) { return `${(Number(value) * 100).toFixed(1)}%` }
function customerLabel(key) { return `${key.slice(0, 8)}…${key.slice(-4)}` }
function optionalBoolean(value) { return value === '' ? null : value === 'true' }
function localDateTime(value) { return value ? new Date(value).toISOString().slice(0, 16) : '' }

function renderRisk(summary) {
  const maximum = Math.max(...Object.values(summary.risk_bands), 1)
  $('#risk-list').innerHTML = Object.entries(summary.risk_bands).map(([band, count]) => `
    <div class="risk-row" data-band="${band}">
      <span class="risk-name">${bandNames[band]}</span>
      <div class="risk-track"><div class="risk-fill" style="width:${count / maximum * 100}%"></div></div>
      <span class="risk-count">${count}</span>
    </div>`).join('')
}

function renderPriority() {
  $('#priority-body').innerHTML = state.priority.length ? state.priority.map(row => `
    <tr>
      <td title="${row.customer_key}">${customerLabel(row.customer_key)}</td>
      <td class="probability">${percentage(row.churn_probability)}</td>
      <td><span class="band ${row.risk_band}">${bandNames[row.risk_band]}</span></td>
      <td>${row.recommended_playbook}</td>
      <td><button class="create-task" data-id="${row.prediction_id}" data-playbook="${row.recommended_playbook}">创建任务</button></td>
    </tr>`).join('') : '<tr><td colspan="5">当前筛选条件下没有客户</td></tr>'
}

function renderTasks(eventCount = 0) {
  const statuses = ['OPEN', 'IN_PROGRESS', 'COMPLETED', 'DISMISSED']
  $('#task-total').textContent = `${state.tasks.length} 条 · ${eventCount} 条变更记录`
  $('#task-board').innerHTML = statuses.map(status => {
    const items = state.tasks.filter(task => task.status === status)
    return `<div class="task-column"><h3><span>${statusNames[status]}</span><span>${items.length}</span></h3>${items.map(task => {
      const outcome = state.outcomes.get(task.task_id)
      return `
      <article class="task-card">
        <label class="task-select"><input type="checkbox" data-task-id="${task.task_id}" ${state.selectedTasks.has(task.task_id) ? 'checked' : ''}><strong>${customerLabel(task.customer_key)}</strong></label>
        <small>${task.playbook}</small>
        <div class="task-owner"><input value="${task.owner}" maxlength="80" aria-label="任务负责人"><button class="save-owner" data-id="${task.task_id}" type="button">分派</button></div>
        <select class="task-status" data-id="${task.task_id}" aria-label="更新任务状态">
          ${statuses.map(option => `<option value="${option}" ${option === status ? 'selected' : ''}>${statusNames[option]}</option>`).join('')}
        </select>
        <div class="task-card-actions"><button class="task-outcome" data-id="${task.task_id}" type="button">${outcome ? '更新结果' : '登记结果'}</button><button class="task-history" data-id="${task.task_id}" type="button">查看时间线</button></div>
        ${outcome ? `<small class="outcome-badge">${outcome.contact_result === 'REACHED' ? '已联系' : '已记录'} · 7 天${outcome.retained_7d === null ? '待观察' : outcome.retained_7d ? '留存' : '未留存'}</small>` : ''}
      </article>`}).join('')}</div>`
  }).join('')
  $('#selected-task-count').textContent = `已选择 ${state.selectedTasks.size} 条任务`
  $('#bulk-assign-owner').disabled = state.selectedTasks.size === 0
  $('#bulk-assign-start').disabled = state.selectedTasks.size === 0
  $('#bulk-start-tasks').disabled = state.selectedTasks.size === 0
  $('#bulk-complete-tasks').disabled = state.selectedTasks.size === 0
}

function renderWorkloads(response) {
  $('#workload-total').textContent = `${response.owner_count} 位负责人 · ${response.active_task_count} 条活跃任务`
  $('#workload-grid').innerHTML = response.items.length ? response.items.map(item => `
    <article class="workload-card">
      <strong>${item.owner}</strong><b>${item.active_count}</b><small>待执行 ${item.open_count} · 处理中 ${item.in_progress_count}</small>
      <span>极高风险 ${item.critical_count} · 高风险 ${item.high_count}</span>
    </article>`).join('') : '<p class="empty-state">当前没有活跃任务</p>'
}

async function performLoad() {
  try {
    const band = $('#risk-filter').value
    const suffix = band ? `&risk_band=${band}` : ''
    const taskQuery = new URLSearchParams({ limit: '200' })
    if ($('#task-risk-filter').value) taskQuery.set('risk_band', $('#task-risk-filter').value)
    if ($('#task-owner-filter').value) taskQuery.set('owner', $('#task-owner-filter').value)
    const [summary, model, priority, tasks, workloads, outcomes] = await Promise.all([
      request('/v1/portfolio/summary'), request('/v1/model'),
      request(`/v1/portfolio/priority?limit=30${suffix}`), request(`/v1/retention/tasks?${taskQuery}`), request('/v1/retention/workloads'), request('/v3/retention/outcomes?limit=500'),
    ])
    $('#customer-count').textContent = summary.customer_count.toLocaleString()
    $('#predicted-count').textContent = summary.predicted_churn_count.toLocaleString()
    $('#average-probability').textContent = percentage(summary.average_churn_probability)
    $('#active-task-count').textContent = (summary.retention_tasks.OPEN + summary.retention_tasks.IN_PROGRESS).toLocaleString()
    state.eventCursor = Math.max(state.eventCursor, summary.retention_latest_event_id || 0)
    $('#snapshot-time').textContent = `快照 ${summary.latest_as_of_time}`
    $('#model-version').textContent = model.model_version
    $('#model-algorithm').textContent = model.algorithm
    $('#model-threshold').textContent = model.decision_threshold
    $('#model-features').textContent = model.feature_columns.length
    $('#model-hash').textContent = model.model_sha256
    $('#lineage-status').textContent = '批次血缘已就绪'
    $('.lineage i').classList.add('ready')
    state.priority = priority.items
    state.tasks = tasks.items
    state.outcomes = new Map(outcomes.items.map(item => [item.task_id, item]))
    $('#outcome-recorded').textContent = summary.retention_outcomes.recorded_count.toLocaleString()
    $('#outcome-reached').textContent = summary.retention_outcomes.reached_count.toLocaleString()
    $('#outcome-retention-7d').textContent = summary.retention_outcomes.retention_7d_rate === null ? '待观察' : percentage(summary.retention_outcomes.retention_7d_rate)
    $('#outcome-retention-30d').textContent = summary.retention_outcomes.retention_30d_rate === null ? '待观察' : percentage(summary.retention_outcomes.retention_30d_rate)
    state.selectedTasks = new Set([...state.selectedTasks].filter(taskId => tasks.items.some(task => task.task_id === taskId)))
    if (!$('#task-risk-filter').value && !$('#task-owner-filter').value) {
      const selectedOwner = $('#task-owner-filter').value
      const owners = [...new Set(tasks.items.map(task => task.owner))].sort((left, right) => left.localeCompare(right, 'zh-CN'))
      $('#task-owner-filter').replaceChildren(new Option('全部负责人', ''), ...owners.map(owner => new Option(owner, owner)))
      $('#task-owner-filter').value = selectedOwner
    }
    renderRisk(summary); renderPriority(); renderWorkloads(workloads); renderTasks(summary.retention_event_count)
    $('#error-banner').hidden = true
  } catch (error) {
    $('#error-banner').textContent = error.message
    $('#error-banner').hidden = false
  }
}

async function load() {
  if (state.loadPromise) {
    state.reloadQueued = true
    return state.loadPromise
  }
  document.body.classList.add('is-refreshing')
  $('#main-content').setAttribute('aria-busy', 'true')
  state.loadPromise = performLoad()
  try {
    await state.loadPromise
  } finally {
    state.loadPromise = null
    document.body.classList.remove('is-refreshing')
    $('#main-content').setAttribute('aria-busy', 'false')
    if (state.reloadQueued) {
      state.reloadQueued = false
      return load()
    }
  }
}

document.addEventListener('click', event => {
  const button = event.target.closest('.create-task')
  if (!button) return
  $('#prediction-id').value = button.dataset.id
  $('#task-playbook').value = button.dataset.playbook
  $('#task-owner').value = 'retention-team'
  $('#task-dialog').showModal()
})

document.addEventListener('click', async event => {
  const button = event.target.closest('.task-history')
  if (!button) return
  try {
    const history = await request(`/v1/retention/tasks/${button.dataset.id}/events`)
    $('#history-task-id').textContent = history.task_id
    $('#history-list').innerHTML = history.items.map(item => `
      <li><span>${new Date(item.occurred_at).toLocaleString('zh-CN', { hour12: false })}</span>
      <strong>${item.event_type === 'CREATED' ? '创建任务' : item.event_type === 'OWNER_CHANGED' ? '负责人变更' : item.event_type === 'OUTCOME_RECORDED' ? '登记处置结果' : '状态变更'}</strong>
      <small>${item.event_type === 'OWNER_CHANGED' ? `${item.from_owner} → ${item.to_owner}` : `${item.from_status ? `${statusNames[item.from_status]} → ` : ''}${statusNames[item.to_status]}`}</small></li>`).join('')
    $('#history-dialog').showModal()
  } catch (error) {
    $('#error-banner').textContent = error.message
    $('#error-banner').hidden = false
  }
})

document.addEventListener('click', event => {
  const button = event.target.closest('.task-outcome')
  if (!button) return
  const outcome = state.outcomes.get(button.dataset.id)
  $('#outcome-task-id').value = button.dataset.id
  $('#outcome-task-label').textContent = button.dataset.id
  $('#outcome-contact-result').value = outcome?.contact_result || 'REACHED'
  $('#outcome-contact-channel').value = outcome?.contact_channel || 'PHONE'
  $('#outcome-customer-response').value = outcome?.customer_response || 'UNKNOWN'
  $('#outcome-contacted-at').value = localDateTime(outcome?.contacted_at || new Date())
  $('#outcome-observed-at').value = localDateTime(outcome?.observed_at)
  $('#outcome-retained-7d').value = outcome?.retained_7d === null || outcome?.retained_7d === undefined ? '' : String(outcome.retained_7d)
  $('#outcome-retained-30d').value = outcome?.retained_30d === null || outcome?.retained_30d === undefined ? '' : String(outcome.retained_30d)
  $('#outcome-note').value = outcome?.note || ''
  $('#outcome-dialog').showModal()
})

$('#outcome-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return
  event.preventDefault()
  const taskId = $('#outcome-task-id').value
  try {
    await request(`/v3/retention/tasks/${taskId}/outcome`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contact_result: $('#outcome-contact-result').value,
        contact_channel: $('#outcome-contact-channel').value,
        customer_response: $('#outcome-customer-response').value,
        contacted_at: new Date($('#outcome-contacted-at').value).toISOString(),
        observed_at: $('#outcome-observed-at').value ? new Date($('#outcome-observed-at').value).toISOString() : null,
        retained_7d: optionalBoolean($('#outcome-retained-7d').value),
        retained_30d: optionalBoolean($('#outcome-retained-30d').value),
        note: $('#outcome-note').value.trim() || null,
      }),
    })
    $('#outcome-dialog').close()
    await load()
  } catch (error) {
    $('#error-banner').textContent = error.message
    $('#error-banner').hidden = false
  }
})

$('#task-form').addEventListener('submit', async event => {
  if (event.submitter?.value === 'cancel') return
  event.preventDefault()
  try {
    await request('/v1/retention/tasks', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prediction_id: $('#prediction-id').value, owner: $('#task-owner').value, playbook: $('#task-playbook').value }),
    })
    $('#task-dialog').close(); await load()
  } catch (error) { $('#error-banner').textContent = error.message; $('#error-banner').hidden = false; $('#task-dialog').close() }
})

document.addEventListener('change', async event => {
  if (event.target.matches('[data-task-id]')) {
    if (event.target.checked) state.selectedTasks.add(event.target.dataset.taskId)
    else state.selectedTasks.delete(event.target.dataset.taskId)
    $('#selected-task-count').textContent = `已选择 ${state.selectedTasks.size} 条任务`
    $('#bulk-assign-owner').disabled = state.selectedTasks.size === 0
    $('#bulk-assign-start').disabled = state.selectedTasks.size === 0
    $('#bulk-start-tasks').disabled = state.selectedTasks.size === 0
    $('#bulk-complete-tasks').disabled = state.selectedTasks.size === 0
    return
  }
  if (['risk-filter', 'task-risk-filter', 'task-owner-filter'].includes(event.target.id)) { await load(); return }
  if (!event.target.classList.contains('task-status')) return
  try {
    await request(`/v1/retention/tasks/${event.target.dataset.id}/status`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: event.target.value }),
    })
    await load()
  } catch (error) { $('#error-banner').textContent = error.message; $('#error-banner').hidden = false }
})

$('#refresh-button').addEventListener('click', load)

document.addEventListener('click', async event => {
  const button = event.target.closest('.save-owner')
  if (!button) return
  const owner = button.parentElement.querySelector('input').value.trim()
  try {
    await request(`/v1/retention/tasks/${button.dataset.id}/owner`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ owner }),
    })
    await load()
  } catch (error) { $('#error-banner').textContent = error.message; $('#error-banner').hidden = false }
})

async function confirmBulkOperation(payload, label) {
  const preview = await request('/v1/retention/tasks/bulk-preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  const riskDetail = Object.entries(preview.changed_by_risk_band)
    .filter(([, count]) => count > 0)
    .map(([band, count]) => `${bandNames[band]} ${count} 条`).join('、')
  const message = `预览：将更新 ${preview.changed_count} 条，跳过 ${preview.unchanged_count} 条 · 共 ${preview.impact.field_change_count} 个字段变化${riskDetail ? ` · ${riskDetail}` : ''}`
  $('#bulk-action-result').textContent = message
  if (preview.changed_count === 0) return false
  const changePreview = preview.changes.slice(0, 3).map(change => {
    const details = change.changed_fields.map(field => field === 'owner'
      ? `负责人 ${change.before.owner} → ${change.after.owner}`
      : `状态 ${statusNames[change.before.status]} → ${statusNames[change.after.status]}`)
    return `${change.task_id}：${details.join('，')}`
  }).join('\n')
  const remaining = preview.changes.length > 3 ? `\n另有 ${preview.changes.length - 3} 条变更` : ''
  const caution = preview.requires_explicit_confirmation
    ? `\n需要重点确认：${preview.confirmation_reasons.join('、')}` : ''
  return window.confirm(`${label}\n${message}${caution}\n${changePreview}${remaining}\n确认继续执行吗？`)
}

$('#bulk-assign-owner').addEventListener('click', async () => {
  const owner = $('#bulk-task-owner').value.trim()
  if (!owner || state.selectedTasks.size === 0) return
  try {
    const payload = { task_ids: [...state.selectedTasks], owner }
    if (!await confirmBulkOperation(payload, `批量分派给 ${owner}`)) return
    const result = await request('/v1/retention/tasks/bulk-owner', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    $('#bulk-action-result').textContent = `已更新 ${result.changed_count} 条，跳过 ${result.unchanged_count} 条`
    state.selectedTasks.clear(); $('#bulk-task-owner').value = ''; await load()
  } catch (error) { $('#error-banner').textContent = error.message; $('#error-banner').hidden = false }
})

async function bulkUpdateStatus(status) {
  if (state.selectedTasks.size === 0) return
  try {
    const payload = { task_ids: [...state.selectedTasks], status }
    if (!await confirmBulkOperation(payload, `批量更新为${statusNames[status]}`)) return
    const result = await request('/v1/retention/tasks/bulk-status', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    $('#bulk-action-result').textContent = `已更新 ${result.changed_count} 条，跳过 ${result.unchanged_count} 条`
    state.selectedTasks.clear(); await load()
  } catch (error) { $('#error-banner').textContent = error.message; $('#error-banner').hidden = false }
}

$('#bulk-start-tasks').addEventListener('click', () => bulkUpdateStatus('IN_PROGRESS'))
$('#bulk-complete-tasks').addEventListener('click', () => bulkUpdateStatus('COMPLETED'))

$('#bulk-assign-start').addEventListener('click', async () => {
  const owner = $('#bulk-task-owner').value.trim()
  if (!owner || state.selectedTasks.size === 0) return
  try {
    const payload = { task_ids: [...state.selectedTasks], owner, status: 'IN_PROGRESS', operator_note: $('#bulk-task-note').value.trim() || null }
    if (!await confirmBulkOperation(payload, `分派给 ${owner} 并开始处理`)) return
    const result = await request('/v2/retention/tasks/bulk', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    })
    const changedFields = result.changes.reduce((total, change) => total + change.changed_fields.length, 0)
    const followUp = result.follow_up.required ? `${result.follow_up.review_priority} · 下一步 ${result.follow_up.next_action}（${result.follow_up.review_window_hours} 小时内）` : `下一步 ${result.follow_up.next_action}`
    $('#bulk-action-result').textContent = `${result.handoff.summary} · 交接 ${result.handoff.readiness} · 去重 ${result.selection.duplicate_count} 条 · ${followUp} · 清单 ${result.handoff.step_count} 步 / 预计 ${result.handoff.estimated_minutes} 分钟：${result.handoff.checklist.join(' → ')} · ${changedFields} 个字段变化 · 新增审计事件 ${result.audit.events_created_count} 条`
    state.selectedTasks.clear(); $('#bulk-task-owner').value = ''; $('#bulk-task-note').value = ''; await load()
  } catch (error) { $('#error-banner').textContent = error.message; $('#error-banner').hidden = false }
})

async function pollRetentionEvents() {
  if (state.polling || document.hidden) return
  state.polling = true
  try {
    const events = await request(`/v1/retention/events?after_id=${state.eventCursor}&limit=100`)
    state.eventCursor = events.next_after_id
    $('#live-sync-status').classList.add('online')
    $('#live-sync-status b').textContent = '实时同步已连接'
    if (events.count) await load()
  } catch (_) {
    $('#live-sync-status').classList.remove('online')
    $('#live-sync-status b').textContent = '实时同步重连中'
  } finally {
    state.polling = false
  }
}

function schedulePoll(delay = document.hidden ? 15000 : 2000) {
  if (state.pollTimer) clearTimeout(state.pollTimer)
  state.pollTimer = setTimeout(async () => {
    await pollRetentionEvents()
    schedulePoll()
  }, delay)
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) schedulePoll(15000)
  else { void load(); schedulePoll(500) }
})

let storedPage = ''
try { storedPage = localStorage.getItem('churn-workspace-page') || '' } catch (_) {}
activateWorkspacePage(location.hash.slice(1) || storedPage, false)
load()
schedulePoll()

