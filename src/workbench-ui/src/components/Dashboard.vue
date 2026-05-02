<script setup lang="ts">
import { ref, watch, onMounted, computed } from 'vue'
import { apiGet } from '../api'
import { userStore } from '../userStore'

interface CaseListItem {
  id: string
  application_id: string
  applicant_name: string | null
  product_type: string
  branch_name: string
  validator_id: string
  status: string
  recommendation: string | null
  manual_review_required: boolean
  error_detail: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

interface Counts {
  total: number
  in_progress: number
  completed: number
  failed: number
  manual_intervention_required: number
  approve: number
  hold: number
  decline: number
}

const props = defineProps<{ refreshTrigger: number }>()
const emit = defineEmits<{ (e: 'view-case', id: string): void }>()

const cases = ref<CaseListItem[]>([])
const counts = ref<Counts | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

// filters
const statusFilter = ref<string>('all')
const recFilter = ref<string>('all')
const productFilter = ref<string>('all')
const search = ref<string>('')

const params = computed(() => {
  const p = new URLSearchParams()
  if (statusFilter.value !== 'all') p.set('status', statusFilter.value)
  if (recFilter.value !== 'all')    p.set('recommendation', recFilter.value)
  if (productFilter.value !== 'all') p.set('product_type', productFilter.value)
  if (search.value)                  p.set('search', search.value)
  const s = p.toString()
  return s ? `?${s}` : ''
})

async function fetchData() {
  loading.value = true
  error.value = null
  try {
    const [list, c] = await Promise.all([
      apiGet<CaseListItem[]>(`/api/v1/cases${params.value}`),
      apiGet<Counts>('/api/v1/cases/counts'),
    ])
    cases.value = list
    counts.value = c
  } catch (e: any) {
    error.value = e.message || 'Failed to load.'
  } finally {
    loading.value = false
  }
}

watch(() => props.refreshTrigger, fetchData)
watch(() => userStore.current, fetchData, { deep: true })
watch([statusFilter, recFilter, productFilter], fetchData)

let searchDebounce: ReturnType<typeof setTimeout> | null = null
watch(search, () => {
  if (searchDebounce) clearTimeout(searchDebounce)
  searchDebounce = setTimeout(fetchData, 220)
})

onMounted(fetchData)

function statusChipClass(s: string) {
  if (s === 'COMPLETED') return 'chip-success'
  if (s === 'FAILED') return 'chip-danger'
  if (s === 'MANUAL_INTERVENTION_REQUIRED') return 'chip-amber'
  return 'chip-info'
}
function statusLabel(s: string) {
  if (s === 'MANUAL_INTERVENTION_REQUIRED') return 'NEEDS REVIEW'
  return s.replaceAll('_', ' ')
}
function recChipClass(r: string | null) {
  if (r === 'APPROVE') return 'chip-success'
  if (r === 'HOLD') return 'chip-warning'
  if (r === 'DECLINE') return 'chip-danger'
  return 'chip-neutral'
}
function fmtDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}
function clearFilters() {
  statusFilter.value = 'all'
  recFilter.value = 'all'
  productFilter.value = 'all'
  search.value = ''
}

const hasFilters = computed(() =>
  statusFilter.value !== 'all' ||
  recFilter.value !== 'all' ||
  productFilter.value !== 'all' ||
  search.value.length > 0,
)
</script>

<template>
  <div class="dashboard">
    <!-- KPI cards -->
    <section class="kpi-grid">
      <div class="kpi">
        <div>
          <div class="kpi-label">Total Cases</div>
          <div class="kpi-value">{{ counts?.total ?? '—' }}</div>
        </div>
        <div class="kpi-icon" style="color: var(--text-2)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 13h10M7 17h6"/></svg>
        </div>
      </div>

      <div class="kpi" :class="{ 'kpi-attention': (counts?.manual_intervention_required ?? 0) > 0 }">
        <div>
          <div class="kpi-label">Needs Review</div>
          <div class="kpi-value" :style="{ color: (counts?.manual_intervention_required ?? 0) > 0 ? 'var(--amber)' : undefined }">
            {{ counts?.manual_intervention_required ?? '—' }}
          </div>
        </div>
        <div class="kpi-icon" style="color: var(--amber)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 9v4M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>
        </div>
      </div>

      <div class="kpi">
        <div>
          <div class="kpi-label">In Progress</div>
          <div class="kpi-value" :style="{ color: 'var(--info)' }">{{ counts?.in_progress ?? '—' }}</div>
        </div>
        <div class="kpi-icon" style="color: var(--info)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
        </div>
      </div>

      <div class="kpi">
        <div>
          <div class="kpi-label">Recommendations</div>
          <div class="kpi-pills">
            <span class="chip chip-success">A {{ counts?.approve ?? 0 }}</span>
            <span class="chip chip-warning">H {{ counts?.hold ?? 0 }}</span>
            <span class="chip chip-danger">D {{ counts?.decline ?? 0 }}</span>
          </div>
        </div>
        <div class="kpi-icon" style="color: var(--success)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 13l4 4L19 7"/></svg>
        </div>
      </div>
    </section>

    <!-- Filter bar -->
    <section class="panel filter-bar">
      <div class="search flex-1">
        <span class="search-icon">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        </span>
        <input class="input" type="text" v-model="search" placeholder="Search by application ID or applicant name…" />
      </div>

      <div class="filters">
        <select v-model="statusFilter">
          <option value="all">All statuses</option>
          <option value="CREATED">Created</option>
          <option value="IN_PROGRESS">In progress</option>
          <option value="COMPLETED">Completed</option>
          <option value="FAILED">Failed</option>
          <option value="MANUAL_INTERVENTION_REQUIRED">Needs review</option>
        </select>
        <select v-model="recFilter">
          <option value="all">All recommendations</option>
          <option value="APPROVE">Approve</option>
          <option value="HOLD">Hold</option>
          <option value="DECLINE">Decline</option>
        </select>
        <select v-model="productFilter">
          <option value="all">All products</option>
          <option value="PERSONAL_FINANCE">Personal Finance</option>
          <option value="AUTO_FINANCE">Auto Finance</option>
        </select>
        <button v-if="hasFilters" class="btn btn-ghost btn-sm" @click="clearFilters">Clear</button>
        <button class="btn btn-soft btn-sm" @click="fetchData" title="Refresh">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 12a9 9 0 0 1 15.3-6.4L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.3 6.4L3 16"/><path d="M3 21v-5h5"/></svg>
          Refresh
        </button>
      </div>
    </section>

    <!-- Cases panel -->
    <section class="panel cases-panel">
      <div class="cases-header">
        <div>
          <h2 class="cases-title">Cases</h2>
          <p class="muted" style="font-size: 12px; margin-top: 2px;">
            Scoped to {{ userStore.current.role.toLowerCase() }} · {{ cases.length }} shown
          </p>
        </div>
      </div>

      <div v-if="error" class="alert alert-danger" style="margin: 12px 18px;">
        <strong class="alert-title">Could not load cases.</strong>
        <div class="mono" style="margin-top: 4px;">{{ error }}</div>
      </div>

      <div v-if="loading" class="skel-list">
        <div v-for="i in 6" :key="i" class="skeleton sk-row" style="margin: 8px 18px;"></div>
      </div>

      <div v-else-if="cases.length === 0" class="empty">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9h10M7 13h6"/></svg>
        <div class="empty-title">No cases match your filters</div>
        <div class="muted" style="font-size: 12px;">Adjust the search or filters above, or submit a new application.</div>
      </div>

      <div v-else class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th>Application</th>
              <th>Applicant</th>
              <th>Product</th>
              <th>Branch</th>
              <th>Status</th>
              <th>Recommendation</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in cases"
              :key="c.id"
              :class="{ 'row-attention': c.status === 'MANUAL_INTERVENTION_REQUIRED' || c.manual_review_required }"
              @click="emit('view-case', c.application_id)"
            >
              <td>
                <div class="cell-id">
                  <span class="mono">{{ c.application_id }}</span>
                  <span v-if="c.manual_review_required" class="chip chip-amber" style="margin-left: 6px;">flag</span>
                </div>
              </td>
              <td>{{ c.applicant_name ?? '—' }}</td>
              <td><span class="chip chip-neutral">{{ c.product_type.replace('_', ' ') }}</span></td>
              <td class="muted">{{ c.branch_name }}</td>
              <td>
                <span class="chip chip-dot" :class="statusChipClass(c.status)">{{ statusLabel(c.status) }}</span>
              </td>
              <td>
                <span v-if="c.recommendation" class="chip" :class="recChipClass(c.recommendation)">{{ c.recommendation }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="mono muted" style="font-size: 12px;">{{ fmtDate(c.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dashboard { display: flex; flex-direction: column; gap: 18px; }

.kpi-attention {
  border-color: rgba(245,158,11,0.5);
  box-shadow: 0 0 0 1px rgba(245,158,11,0.2);
}

.kpi-pills { display: flex; gap: 6px; margin-top: 8px; }

.filter-bar {
  padding: 14px 18px;
  display: flex;
  gap: 14px;
  align-items: center;
  flex-wrap: wrap;
}
.filter-bar .search { min-width: 260px; flex: 1; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.filters select {
  width: auto;
  min-width: 140px;
  padding-right: 28px;
}

.cases-panel { padding: 0; }
.cases-header {
  padding: 18px 20px 8px;
  display: flex; justify-content: space-between; align-items: center;
}
.cases-title { font-size: 16px; font-weight: 600; }

.table-wrap { overflow-x: auto; }
.cell-id { display: inline-flex; align-items: center; }

.empty {
  padding: 60px 20px;
  text-align: center;
  color: var(--text-3);
  display: flex; flex-direction: column; align-items: center; gap: 10px;
}
.empty-title { font-size: 14px; color: var(--text-2); font-weight: 500; }

.skel-list { padding: 12px 0 18px; }
</style>
