<script setup lang="ts">
import { ref, onMounted } from 'vue'

const props = defineProps<{
  caseId: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const caseData = ref<any>(null)
const loading = ref(true)

onMounted(async () => {
  try {
    const res = await fetch(`/api/cases/${props.caseId}`)
    caseData.value = await res.json()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
})

const getOutcomeClass = (outcome: string) => {
  if (outcome === 'HARD_BREACH') return 'badge-danger'
  if (outcome === 'SOFT_MISMATCH') return 'badge-warning'
  if (outcome === 'LOW_CONFIDENCE') return 'badge-warning'
  return 'badge-success'
}

const downloadReport = async () => {
  const res = await fetch(`/api/cases/${props.caseId}/report`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `report_${props.caseId}.pdf`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-content glass case-details-modal">
      <div class="modal-header">
        <h3>Case Details</h3>
        <div class="header-actions">
          <button
            v-if="caseData?.report?.pdf_available"
            class="btn btn-primary btn-sm"
            @click="downloadReport"
          >
            ↓ Download PDF Report
          </button>
          <span
            v-else-if="caseData && !loading"
            class="report-unavailable"
          >
            Report not yet available
          </span>
          <button class="close-btn" @click="emit('close')">&times;</button>
        </div>
      </div>
      
      <div class="modal-body" v-if="loading">
        Loading case details...
      </div>
      
      <div class="modal-body" v-else-if="caseData && !caseData.error">
        <div class="section">
          <h4>Overview</h4>
          <div class="info-grid">
            <div><strong>Application ID:</strong> {{ caseData.case.application_id }}</div>
            <div><strong>Status:</strong> {{ caseData.case.status }}</div>
            <div><strong>Recommendation:</strong> {{ caseData.case.recommendation || 'PENDING' }}</div>
          </div>
        </div>

        <div class="section">
          <h4>Documents Processed</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Status</th>
                <th>Verified</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="doc in caseData.documents" :key="doc.id">
                <td>{{ doc.document_name || doc.dms_document_id }}</td>
                <td>{{ doc.document_type }}</td>
                <td>{{ doc.status }}</td>
                <td>
                  <span v-if="doc.verification_passed === true" class="badge badge-success">✓ PASS</span>
                  <span v-else-if="doc.verification_passed === false" class="badge badge-danger">✗ FAIL</span>
                  <span v-else class="text-secondary">-</span>
                </td>
                <td>{{ doc.verification_confidence != null ? (doc.verification_confidence * 100).toFixed(0) + '%' : '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="section">
          <h4>Validation Results</h4>
          <table class="data-table">
            <thead>
              <tr>
                <th>Rule</th>
                <th>Outcome</th>
                <th>Field</th>
                <th>Extracted</th>
                <th>Expected</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="val in caseData.validations" :key="val.id">
                <td><code>{{ val.rule_code }}</code></td>
                <td>
                  <span class="badge" :class="getOutcomeClass(val.outcome)">{{ val.outcome }}</span>
                </td>
                <td class="text-sm">{{ val.field_name || '-' }}</td>
                <td class="text-sm">{{ val.extracted_value || '-' }}</td>
                <td class="text-sm">{{ val.expected_value || '-' }}</td>
                <td class="text-sm desc-cell">{{ val.description }}</td>
              </tr>
              <tr v-if="caseData.validations.length === 0">
                <td colspan="6" class="text-secondary text-center py-4">No validations run yet.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      
      <div class="modal-body" v-else>
        <p class="text-danger">Failed to load case details.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.case-details-modal {
  max-width: 800px;
}
.modal-content {
  display: flex;
  flex-direction: column;
}
.modal-header {
  padding: 1.5rem;
  border-bottom: 1px solid var(--surface-border);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.modal-header h3 {
  margin: 0;
  font-size: 1.25rem;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}
.report-unavailable {
  font-size: 0.8rem;
  color: var(--text-secondary);
  font-style: italic;
}
.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 1.5rem;
  cursor: pointer;
}
.close-btn:hover {
  color: white;
}
.modal-body {
  padding: 1.5rem;
}
.section {
  margin-bottom: 2rem;
}
.section h4 {
  margin-bottom: 1rem;
  font-size: 1.1rem;
  color: var(--primary);
}
.info-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  background: rgba(0, 0, 0, 0.2);
  padding: 1rem;
  border-radius: 8px;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  background: rgba(0, 0, 0, 0.2);
  border-radius: 8px;
  overflow: hidden;
}
.data-table th, .data-table td {
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--surface-border);
}
.data-table th {
  background: rgba(0, 0, 0, 0.3);
  font-size: 0.75rem;
  text-transform: uppercase;
  color: var(--text-secondary);
}
.text-sm {
  font-size: 0.875rem;
}
pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: monospace;
}
.text-secondary {
  color: var(--text-secondary);
}
.text-center {
  text-align: center;
}
.py-4 {
  padding-top: 1rem;
  padding-bottom: 1rem;
}
.text-danger {
  color: var(--danger);
}
</style>
