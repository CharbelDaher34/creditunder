<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { apiGet, apiBlob } from '../api'

interface ExtractedField { label: string; value: any; confidence: number | null; page_reference: number | null }
interface Doc {
  id: string
  dms_document_id: string
  document_name: string | null
  document_type: string | null
  status: string
  verification_passed: boolean | null
  verification_confidence: number | null
  fetched_at: string | null
  verified_at: string | null
  error_detail: string | null
  extracted_fields: ExtractedField[]
}
interface ValidationItem {
  id: string; rule_code: string; outcome: string; description: string;
  field_name: string | null; extracted_value: string | null;
  expected_value: string | null; confidence: number | null;
  manual_review_required: boolean; evaluated_at: string;
}
interface Validations {
  passed: ValidationItem[]
  hard_breach: ValidationItem[]
  soft_mismatch: ValidationItem[]
  low_confidence: ValidationItem[]
  manual_review: ValidationItem[]
}
interface AuditEntry { id: string; event_type: string; actor: string | null; detail: any; occurred_at: string }
interface Report { status: string | null; pdf_available: boolean; pdf_uploaded_at: string | null; error_detail: string | null }
interface CaseDetail {
  case: any
  documents: Doc[]
  validations: Validations
  report: Report
  audit_timeline: AuditEntry[]
}

const props = defineProps<{ caseId: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const data = ref<CaseDetail | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const activeTab = ref<'overview' | 'documents' | 'validations' | 'timeline'>('overview')

// document preview: keyed by dms_document_id → { url, contentType } | 'loading' | 'error'
const docPreviews = ref<Record<string, { url: string; contentType: string } | 'loading' | 'error'>>({})

async function loadDocPreviews() {
  if (!data.value) return
  for (const d of data.value.documents) {
    if (docPreviews.value[d.dms_document_id]) continue
    docPreviews.value[d.dms_document_id] = 'loading'
    try {
      const blob = await apiBlob(`/api/v1/cases/${props.caseId}/documents/${d.dms_document_id}/preview`)
      const url = URL.createObjectURL(blob)
      docPreviews.value[d.dms_document_id] = { url, contentType: blob.type }
    } catch {
      docPreviews.value[d.dms_document_id] = 'error'
    }
  }
}

watch(activeTab, (tab) => {
  if (tab === 'documents') loadDocPreviews()
})

const validationCount = computed(() => {
  if (!data.value) return 0
  const v = data.value.validations
  return v.passed.length + v.hard_breach.length + v.soft_mismatch.length + v.low_confidence.length + v.manual_review.length
})

onMounted(async () => {
  try {
    data.value = await apiGet<CaseDetail>(`/api/v1/cases/${props.caseId}`)
    if (activeTab.value === 'documents') loadDocPreviews()
  } catch (e: any) {
    error.value = e.message || 'Failed to load case.'
  } finally {
    loading.value = false
  }
})

async function downloadReport() {
  try {
    const blob = await apiBlob(`/api/v1/cases/${props.caseId}/report`)
    const url = URL.createObjectURL(blob)
    window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (e: any) {
    error.value = e.message || 'Failed to download report.'
  }
}

function recChipClass(r: string | null) {
  if (r === 'APPROVE') return 'chip-success'
  if (r === 'HOLD') return 'chip-warning'
  if (r === 'DECLINE') return 'chip-danger'
  return 'chip-neutral'
}
function statusChipClass(s: string | null) {
  if (!s) return 'chip-neutral'
  if (s === 'COMPLETED') return 'chip-success'
  if (s === 'FAILED') return 'chip-danger'
  if (s === 'MANUAL_INTERVENTION_REQUIRED') return 'chip-amber'
  return 'chip-info'
}
function reportChip(s: string | null) {
  if (!s) return 'chip-neutral'
  if (s === 'UPLOADED') return 'chip-success'
  if (s === 'FAILED') return 'chip-danger'
  return 'chip-info'
}
function outcomeChip(o: string) {
  if (o === 'PASS') return 'chip-success'
  if (o === 'HARD_BREACH') return 'chip-danger'
  if (o === 'SOFT_MISMATCH') return 'chip-warning'
  if (o === 'LOW_CONFIDENCE') return 'chip-info'
  if (o === 'MANUAL_REVIEW_REQUIRED') return 'chip-amber'
  return 'chip-neutral'
}
function fmtDateTime(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}
function confKlass(c: number | null) {
  if (c == null) return ''
  if (c < 0.6) return 'danger'
  if (c < 0.8) return 'warn'
  return ''
}
function tlClass(eventType: string) {
  if (eventType.includes('FAILED') || eventType === 'CASE_FAILED' || eventType === 'DOCUMENT_TYPE_MISMATCH') return 'tl-danger'
  if (eventType === 'MISSING_REQUIRED_DOCUMENTS') return 'tl-warning'
  if (eventType === 'CASE_COMPLETED' || eventType === 'REPORT_UPLOADED') return 'tl-success'
  return ''
}
function eventTitle(t: string) {
  return t.replaceAll('_', ' ').toLowerCase().replace(/^./, c => c.toUpperCase())
}
function detailString(d: any): string {
  if (!d) return ''
  if (typeof d === 'string') return d
  return JSON.stringify(d, null, 2)
}
</script>

<template>
  <div class="drawer-backdrop" @click="emit('close')"></div>
  <aside class="drawer" role="dialog" aria-label="Case details">
    <header class="drawer-header">
      <div class="row gap-12" style="min-width: 0;">
        <button class="btn btn-icon btn-ghost" @click="emit('close')" title="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
        <div style="min-width: 0;">
          <div class="drawer-eyebrow muted">Application</div>
          <div class="drawer-title mono truncate">{{ data?.case?.application_id ?? caseId }}</div>
        </div>
      </div>
      <div class="row gap-12">
        <button
          class="btn btn-soft btn-sm"
          :disabled="!data?.report?.pdf_available"
          @click="downloadReport"
          :title="data?.report?.pdf_available ? 'Open the PDF report' : 'Report not yet uploaded'"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>
          PDF Report
        </button>
      </div>
    </header>

    <div class="drawer-body">
      <!-- Loading -->
      <div v-if="loading">
        <div class="skeleton sk-line" style="width: 40%; height: 24px;"></div>
        <div class="skeleton sk-line" style="width: 75%; margin-top: 14px;"></div>
        <div class="skeleton sk-row" style="margin-top: 18px;"></div>
        <div class="skeleton sk-row" style="margin-top: 8px;"></div>
      </div>

      <!-- Error -->
      <div v-else-if="error" class="alert alert-danger">
        <div>
          <div class="alert-title">Could not load this case.</div>
          <div class="mono" style="margin-top: 4px;">{{ error }}</div>
        </div>
      </div>

      <template v-else-if="data">
        <!-- Hero card -->
        <section class="hero panel panel-pad">
          <div class="hero-top">
            <div class="hero-applicant">
              <div class="hero-eyebrow muted">Applicant</div>
              <div class="hero-name">{{ data.case.applicant_name ?? '—' }}</div>
              <div class="hero-meta muted">
                <span class="mono">{{ data.case.applicant_data?.id_number ?? '' }}</span>
                <span v-if="data.case.applicant_data?.employer">· {{ data.case.applicant_data.employer }}</span>
              </div>
            </div>

            <div class="hero-rec">
              <div class="hero-eyebrow muted" style="text-align: right;">Recommendation</div>
              <span
                v-if="data.case.recommendation"
                class="big-rec"
                :class="recChipClass(data.case.recommendation)"
              >{{ data.case.recommendation }}</span>
              <span v-else class="big-rec chip-neutral">PENDING</span>
              <div v-if="data.case.recommendation_rationale" class="hero-rationale muted">
                {{ data.case.recommendation_rationale }}
              </div>
            </div>
          </div>

          <div class="hero-status-row">
            <div class="status-pill">
              <span class="muted">Status</span>
              <span class="chip chip-dot" :class="statusChipClass(data.case.status)">
                {{ data.case.status === 'MANUAL_INTERVENTION_REQUIRED' ? 'NEEDS REVIEW' : data.case.status.replaceAll('_', ' ') }}
              </span>
            </div>
            <div class="status-pill">
              <span class="muted">Report</span>
              <span class="chip" :class="reportChip(data.report.status)">{{ data.report.status ?? 'PENDING' }}</span>
            </div>
            <div class="status-pill">
              <span class="muted">Submitted</span>
              <span class="mono">{{ fmtDateTime(data.case.created_at) }}</span>
            </div>
            <div v-if="data.case.completed_at" class="status-pill">
              <span class="muted">Completed</span>
              <span class="mono">{{ fmtDateTime(data.case.completed_at) }}</span>
            </div>
          </div>
        </section>

        <!-- Error banners -->
        <div class="banners">
          <div v-if="data.case.error_detail" class="alert alert-danger">
            <div>
              <div class="alert-title">Pipeline error</div>
              <div class="mono" style="margin-top: 4px;">{{ data.case.error_detail }}</div>
            </div>
          </div>
          <div v-if="data.report.status === 'FAILED' && data.report.error_detail" class="alert alert-warning">
            <div>
              <div class="alert-title">Report delivery failed</div>
              <div class="mono" style="margin-top: 4px;">{{ data.report.error_detail }}</div>
            </div>
          </div>
        </div>

        <!-- Tabs -->
        <div class="tabs">
          <button class="tab" :class="{ 'is-active': activeTab === 'overview' }" @click="activeTab = 'overview'">Overview</button>
          <button class="tab" :class="{ 'is-active': activeTab === 'documents' }" @click="activeTab = 'documents'">
            Documents
            <span class="tab-count">{{ data.documents.length }}</span>
          </button>
          <button class="tab" :class="{ 'is-active': activeTab === 'validations' }" @click="activeTab = 'validations'">
            Validations
            <span class="tab-count">{{ validationCount }}</span>
          </button>
          <button class="tab" :class="{ 'is-active': activeTab === 'timeline' }" @click="activeTab = 'timeline'">
            Timeline
            <span class="tab-count">{{ data.audit_timeline.length }}</span>
          </button>
        </div>

        <!-- OVERVIEW -->
        <section v-if="activeTab === 'overview'" class="panel panel-pad">
          <div class="section-title">Applicant Snapshot</div>
          <dl class="kv">
            <template v-for="(v, k) in (data.case.applicant_data || {})" :key="k">
              <dt class="muted">{{ String(k).replaceAll('_', ' ') }}</dt>
              <dd>{{ v ?? '—' }}</dd>
            </template>
          </dl>

          <div class="divider"></div>

          <div class="section-title">Pipeline Routing</div>
          <dl class="kv">
            <dt class="muted">Validator</dt><dd class="mono">{{ data.case.validator_id }}</dd>
            <dt class="muted">Supervisor</dt><dd class="mono">{{ data.case.supervisor_id }}</dd>
            <dt class="muted">Branch</dt><dd>{{ data.case.branch_name }}</dd>
            <dt class="muted">Product</dt><dd>{{ data.case.product_type.replaceAll('_', ' ') }}</dd>
          </dl>
        </section>

        <!-- DOCUMENTS -->
        <section v-else-if="activeTab === 'documents'" style="display: flex; flex-direction: column; gap: 16px;">
          <div v-if="data.documents.length === 0" class="panel panel-pad empty-mini">No documents recorded.</div>

          <article v-for="d in data.documents" :key="d.id" class="doc-card panel">
            <!-- Card header -->
            <header class="doc-card-header">
              <div style="display: flex; align-items: center; gap: 14px; min-width: 0;">
                <div style="min-width: 0;">
                  <div class="doc-name truncate">{{ d.document_name || d.dms_document_id }}</div>
                  <div class="muted mono" style="font-size: 11px; margin-top: 2px;">
                    {{ d.dms_document_id }} &nbsp;·&nbsp; {{ d.document_type ?? 'unknown type' }}
                  </div>
                </div>
              </div>
              <div style="display: flex; align-items: center; gap: 12px; flex-shrink: 0;">
                <span v-if="d.verification_passed === true" class="chip chip-success chip-dot">VERIFIED</span>
                <span v-else-if="d.verification_passed === false" class="chip chip-danger chip-dot">MISMATCH</span>
                <span v-else class="chip chip-neutral">{{ d.status }}</span>
                <span v-if="d.verification_confidence != null" class="confbar">
                  <span class="confbar-track">
                    <span class="confbar-fill" :class="confKlass(d.verification_confidence)"
                      :style="{ width: (d.verification_confidence * 100).toFixed(0) + '%' }"></span>
                  </span>
                  <span class="muted" style="font-size: 12px;">
                    {{ (d.verification_confidence * 100).toFixed(0) }}%
                  </span>
                </span>
              </div>
            </header>

            <div v-if="d.error_detail" class="alert alert-danger" style="margin: 10px 0 0;">
              <div>
                <div class="alert-title">Document error</div>
                <div class="mono" style="margin-top: 4px;">{{ d.error_detail }}</div>
              </div>
            </div>

            <!-- Split: preview left, fields right -->
            <div class="doc-split">
              <!-- Document preview -->
              <div class="doc-preview-col">
                <div class="doc-preview-label">Document</div>
                <div class="doc-preview-frame">
                  <template v-if="docPreviews[d.dms_document_id] === 'loading'">
                    <div class="doc-preview-placeholder">
                      <span class="muted" style="font-size: 12px;">Loading…</span>
                    </div>
                  </template>
                  <template v-else-if="docPreviews[d.dms_document_id] === 'error'">
                    <div class="doc-preview-placeholder">
                      <span class="muted" style="font-size: 12px;">Preview unavailable</span>
                    </div>
                  </template>
                  <template v-else-if="docPreviews[d.dms_document_id]">
                    <img
                      v-if="docPreviews[d.dms_document_id].contentType.startsWith('image/')"
                      :src="docPreviews[d.dms_document_id].url"
                      class="doc-preview-img"
                      :alt="d.document_name || d.dms_document_id"
                    />
                    <iframe
                      v-else-if="docPreviews[d.dms_document_id].contentType === 'application/pdf'"
                      :src="docPreviews[d.dms_document_id].url"
                      class="doc-preview-pdf"
                    ></iframe>
                    <div v-else class="doc-preview-placeholder">
                      <span class="muted" style="font-size: 12px;">
                        {{ docPreviews[d.dms_document_id].contentType }}
                      </span>
                    </div>
                  </template>
                  <template v-else>
                    <div class="doc-preview-placeholder">
                      <span class="muted" style="font-size: 12px;">—</span>
                    </div>
                  </template>
                </div>
              </div>

              <!-- Extracted fields -->
              <div class="doc-fields-col">
                <div class="doc-preview-label">Extracted Fields</div>
                <div v-if="d.extracted_fields.length === 0" class="empty-mini" style="margin-top: 8px;">
                  No fields extracted.
                </div>
                <table v-else class="table mini" style="margin-top: 0;">
                  <thead>
                    <tr>
                      <th style="width: 38%;">Field</th>
                      <th>Value</th>
                      <th style="width: 120px;">Confidence</th>
                      <th style="width: 52px;">Page</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="f in d.extracted_fields" :key="f.label">
                      <td>{{ f.label }}</td>
                      <td class="mono">{{ f.value ?? '—' }}</td>
                      <td>
                        <span v-if="f.confidence != null" class="confbar">
                          <span class="confbar-track">
                            <span class="confbar-fill" :class="confKlass(f.confidence)"
                              :style="{ width: (f.confidence * 100).toFixed(0) + '%' }"></span>
                          </span>
                          <span class="muted" style="font-size: 12px;">
                            {{ (f.confidence * 100).toFixed(0) }}%
                          </span>
                        </span>
                        <span v-else class="muted">—</span>
                      </td>
                      <td class="muted">{{ f.page_reference ?? '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </article>
        </section>

        <!-- VALIDATIONS -->
        <section v-else-if="activeTab === 'validations'" class="panel panel-pad">
          <div v-if="validationCount === 0" class="empty-mini">No validation rows produced for this case.</div>

          <template v-else>
            <div v-for="g in [
              { key: 'hard_breach', title: 'Hard breaches', items: data.validations.hard_breach },
              { key: 'soft_mismatch', title: 'Soft mismatches', items: data.validations.soft_mismatch },
              { key: 'low_confidence', title: 'Low-confidence extractions', items: data.validations.low_confidence },
              { key: 'manual_review', title: 'Manual review required', items: data.validations.manual_review },
            ]" :key="g.key">
              <div v-if="g.items.length > 0" class="val-group">
                <div class="val-group-title">
                  <span>{{ g.title }}</span>
                  <span class="chip chip-neutral">{{ g.items.length }}</span>
                </div>
                <div v-for="v in g.items" :key="v.id" class="val-item">
                  <div class="val-item-head">
                    <span class="chip" :class="outcomeChip(v.outcome)">{{ v.outcome.replaceAll('_', ' ') }}</span>
                    <span class="mono">{{ v.rule_code }}</span>
                    <span v-if="v.field_name" class="muted" style="font-size: 12px;">on {{ v.field_name }}</span>
                  </div>
                  <div class="val-item-desc">{{ v.description }}</div>
                  <div v-if="v.extracted_value || v.expected_value" class="val-item-cmp">
                    <div>
                      <span class="muted">Extracted:</span>
                      <span class="mono">{{ v.extracted_value ?? '—' }}</span>
                    </div>
                    <div>
                      <span class="muted">Expected:</span>
                      <span class="mono">{{ v.expected_value ?? '—' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- Passed rules — collapsed by default, same count always present -->
            <details v-if="data.validations.passed.length > 0" class="val-passed-details">
              <summary class="val-group-title val-passed-summary">
                <span>Passed rules</span>
                <span class="chip chip-success">{{ data.validations.passed.length }}</span>
              </summary>
              <div v-for="v in data.validations.passed" :key="v.id" class="val-item val-item-pass">
                <div class="val-item-head">
                  <span class="chip chip-success">PASS</span>
                  <span class="mono">{{ v.rule_code }}</span>
                  <span v-if="v.field_name" class="muted" style="font-size: 12px;">on {{ v.field_name }}</span>
                </div>
                <div class="val-item-desc">{{ v.description }}</div>
                <div v-if="v.extracted_value || v.expected_value" class="val-item-cmp">
                  <div>
                    <span class="muted">Extracted:</span>
                    <span class="mono">{{ v.extracted_value ?? '—' }}</span>
                  </div>
                  <div>
                    <span class="muted">Expected:</span>
                    <span class="mono">{{ v.expected_value ?? '—' }}</span>
                  </div>
                </div>
              </div>
            </details>
          </template>
        </section>

        <!-- TIMELINE -->
        <section v-else-if="activeTab === 'timeline'" class="panel panel-pad">
          <div v-if="data.audit_timeline.length === 0" class="empty-mini">No audit events recorded yet.</div>
          <ol v-else class="timeline">
            <li v-for="a in data.audit_timeline" :key="a.id" class="tl-item" :class="tlClass(a.event_type)">
              <div class="tl-time">{{ fmtDateTime(a.occurred_at) }}</div>
              <div class="tl-title">{{ eventTitle(a.event_type) }}</div>
              <div v-if="a.actor" class="muted" style="font-size: 11px; margin-top: 2px;">by {{ a.actor }}</div>
              <pre v-if="a.detail" class="tl-detail">{{ detailString(a.detail) }}</pre>
            </li>
          </ol>
        </section>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.drawer-eyebrow { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
.drawer-title { font-size: 16px; font-weight: 600; margin-top: 2px; }

.hero { margin-bottom: 16px; }
.hero-top {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 16px;
}
.hero-eyebrow { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }
.hero-name { font-size: 22px; font-weight: 600; margin-top: 2px; }
.hero-meta { font-size: 12px; margin-top: 4px; }

.hero-rec { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; max-width: 50%; }
.big-rec {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: .04em;
  padding: 6px 16px;
  border-radius: var(--r-md);
}
.hero-rationale { font-size: 12px; max-width: 360px; text-align: right; line-height: 1.45; }

.hero-status-row {
  display: flex; flex-wrap: wrap; gap: 12px 22px;
  margin-top: 18px; padding-top: 16px;
  border-top: 1px solid var(--border);
}
.status-pill {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 12px;
}
.status-pill .muted { font-size: 11px; }

.banners { display: flex; flex-direction: column; gap: 10px; margin: 14px 0 18px; }

/* key/value */
.kv {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 8px 16px;
  font-size: 13px;
}
.kv dt { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; padding-top: 2px; }
.kv dd { margin: 0; word-break: break-word; }

.empty-mini {
  padding: 24px; text-align: center; color: var(--text-3); font-size: 13px;
}

/* documents */
.doc-card { padding: 0; overflow: hidden; }
.doc-card-header {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; padding: 12px 16px;
  background: var(--bg-soft); border-bottom: 1px solid var(--border);
}
.doc-name { font-weight: 600; font-size: 14px; }

.doc-split {
  display: flex;
  gap: 0;
  min-height: 220px;
}
.doc-preview-col {
  width: 46%;
  min-width: 200px;
  padding: 14px;
  background: var(--bg-soft);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.doc-fields-col {
  flex: 1;
  padding: 14px 0 0;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.doc-preview-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--text-3);
  margin-bottom: 2px;
  padding: 0 0 0 2px;
}
.doc-fields-col .doc-preview-label { padding: 0 14px; margin-bottom: 8px; }
.doc-preview-frame {
  flex: 1;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  min-height: 160px;
}
.doc-preview-img {
  max-width: 100%;
  max-height: 360px;
  border-radius: var(--r-md);
  border: 1px solid var(--border);
  object-fit: contain;
  display: block;
}
.doc-preview-pdf {
  width: 100%; height: 320px;
  border: 1px solid var(--border);
  border-radius: var(--r-md);
}
.doc-preview-placeholder {
  width: 100%; min-height: 120px;
  display: flex; align-items: center; justify-content: center;
  background: var(--bg); border-radius: var(--r-md);
  border: 1px dashed var(--border);
}

.table.mini th, .table.mini td { padding: 8px 12px; font-size: 12.5px; }
.table.mini { background: var(--bg-soft); border-radius: var(--r-md); overflow: hidden; }

/* validations */
.val-group { margin-bottom: 18px; }
.val-group-title {
  display: flex; gap: 10px; align-items: center;
  font-size: 13px; font-weight: 600;
  margin-bottom: 10px;
  color: var(--text-2);
}
.val-item {
  border: 1px solid var(--border);
  background: var(--bg-soft);
  border-radius: var(--r-md);
  padding: 12px 14px;
  margin-bottom: 8px;
}
.val-item-pass {
  opacity: 0.7;
}
.val-passed-details { margin-top: 4px; }
.val-passed-details[open] .val-passed-summary { margin-bottom: 10px; }
.val-passed-summary {
  list-style: none; cursor: pointer;
  user-select: none;
}
.val-passed-summary::-webkit-details-marker { display: none; }
.val-item-head { display: flex; gap: 10px; align-items: center; margin-bottom: 6px; }
.val-item-desc { font-size: 13px; color: var(--text-2); }
.val-item-cmp {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 16px;
  margin-top: 8px;
  font-size: 12px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
}
.val-item-cmp .muted { margin-right: 6px; font-size: 11px; }

@media (max-width: 720px) {
  .hero-top { flex-direction: column; }
  .hero-rec { align-items: flex-start; max-width: 100%; }
  .hero-rationale { text-align: left; }
  .doc-card-header { flex-direction: column; align-items: flex-start; }
  .doc-split { flex-direction: column; }
  .doc-preview-col { width: 100%; border-right: none; border-bottom: 1px solid var(--border); }
  .kv { grid-template-columns: 1fr; }
  .val-item-cmp { grid-template-columns: 1fr; }
}
</style>
