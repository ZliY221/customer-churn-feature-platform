const $ = selector => document.querySelector(selector)
const state = { priority: [], tasks: [] }
const bandNames = { CRITICAL: '极高风险', HIGH: '高风险', WATCH: '关注', STABLE: '稳定' }
const statusNames = { OPEN: '待执行', IN_PROGRESS: '处理中', COMPLETED: '已完成', DISMISSED: '已关闭' }

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
    return `<div class="task-column"><h3><span>${statusNames[status]}</span><span>${items.length}</span></h3>${items.map(task => `
      <article class="task-card">
        <strong>${customerLabel(task.customer_key)}</strong>
        <small>${task.playbook}</small><small>负责人：${task.owner}</small>
        <select class="task-status" data-id="${task.task_id}" aria-label="更新任务状态">
          ${statuses.map(option => `<option value="${option}" ${option === status ? 'selected' : ''}>${statusNames[option]}</option>`).join('')}
        </select>
        <button class="task-history" data-id="${task.task_id}" type="button">查看时间线</button>
      </article>`).join('')}</div>`
  }).join('')
}

function renderWorkloads(response) {
  $('#workload-total').textContent = `${response.owner_count} 位负责人 · ${response.active_task_count} 条活跃任务`
  $('#workload-grid').innerHTML = response.items.length ? response.items.map(item => `
    <article class="workload-card">
      <strong>${item.owner}</strong><b>${item.active_count}</b><small>待执行 ${item.open_count} · 处理中 ${item.in_progress_count}</small>
      <span>极高风险 ${item.critical_count} · 高风险 ${item.high_count}</span>
    </article>`).join('') : '<p class="empty-state">当前没有活跃任务</p>'
}

async function load() {
  try {
    const band = $('#risk-filter').value
    const suffix = band ? `&risk_band=${band}` : ''
    const taskQuery = new URLSearchParams({ limit: '200' })
    if ($('#task-risk-filter').value) taskQuery.set('risk_band', $('#task-risk-filter').value)
    if ($('#task-owner-filter').value) taskQuery.set('owner', $('#task-owner-filter').value)
    const [summary, model, priority, tasks, workloads] = await Promise.all([
      request('/v1/portfolio/summary'), request('/v1/model'),
      request(`/v1/portfolio/priority?limit=30${suffix}`), request(`/v1/retention/tasks?${taskQuery}`), request('/v1/retention/workloads'),
    ])
    $('#customer-count').textContent = summary.customer_count.toLocaleString()
    $('#predicted-count').textContent = summary.predicted_churn_count.toLocaleString()
    $('#average-probability').textContent = percentage(summary.average_churn_probability)
    $('#active-task-count').textContent = (summary.retention_tasks.OPEN + summary.retention_tasks.IN_PROGRESS).toLocaleString()
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
      <strong>${item.event_type === 'CREATED' ? '创建任务' : '状态变更'}</strong>
      <small>${item.from_status ? `${statusNames[item.from_status]} → ` : ''}${statusNames[item.to_status]}</small></li>`).join('')
    $('#history-dialog').showModal()
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
load()
setInterval(load, 30000)

