<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { apiPost } from '../api'
import { userStore } from '../userStore'

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'submitted'): void
}>()

// ------------------------------------------------------------------ //
//  Identity                                                             //
// ------------------------------------------------------------------ //

function otherUser(role: 'VALIDATOR' | 'SUPERVISOR') {
  return userStore.options.find(u => u.role === role && u.user_id !== userStore.current.user_id)
    ?? userStore.options.find(u => u.role === role)
}

function defaultValidatorId() {
  if (userStore.current.role === 'VALIDATOR') return userStore.current.user_id
  return otherUser('VALIDATOR')?.user_id ?? 'CRM-USR-4821'
}

function defaultSupervisorId() {
  if (userStore.current.role === 'SUPERVISOR') return userStore.current.user_id
  return otherUser('SUPERVISOR')?.user_id ?? 'CRM-USR-1093'
}

// ------------------------------------------------------------------ //
//  Document types per product                                           //
// ------------------------------------------------------------------ //

interface DocSlot {
  type: string
  label: string
  hint: string
}

const PRODUCT_DOCS: Record<string, DocSlot[]> = {
  PERSONAL_FINANCE: [
    { type: 'ID_DOCUMENT',        label: 'National ID Document',  hint: 'National ID card — both sides in one image' },
    { type: 'SALARY_CERTIFICATE', label: 'Salary Certificate',    hint: 'Official certificate from the employer' },
  ],
  AUTO_FINANCE: [
    { type: 'ID_DOCUMENT',   label: 'National ID Document', hint: 'National ID card — both sides in one image' },
    { type: 'VEHICLE_QUOTE', label: 'Vehicle Quote',        hint: 'Dealer quote / pro-forma invoice' },
  ],
}

// ------------------------------------------------------------------ //
//  State                                                               //
// ------------------------------------------------------------------ //

const isSubmitting  = ref(false)
const errorMsg      = ref<string | null>(null)
const selectedTemplate = ref('happy_path')
const useFileUpload = ref(true)

const formData = ref({
  product_type:          'PERSONAL_FINANCE',
  branch_name:           'Riyadh Main Branch',
  validator_id:          defaultValidatorId(),
  supervisor_id:         defaultSupervisorId(),
  document_ids:          'DMS-00192, DMS-00193',
  applicant_data_json:   JSON.stringify({
    name:            'Mohammed Al-Harbi',
    id_number:       '1082345678',
    date_of_birth:   '1985-04-12',
    employer:        'Saudi Aramco',
    declared_salary: 18500.00,
    simah_score:     720,
    t24_account_id:  'T24-ACC-998821',
  }, null, 2),
})

// One File | null slot per doc-type slot index.
const uploadedFiles = ref<(File | null)[]>([null, null])
const uploadProgress = ref<string[]>(['', ''])

// ------------------------------------------------------------------ //
//  Derived                                                             //
// ------------------------------------------------------------------ //

const requiredDocs = computed<DocSlot[]>(() =>
  PRODUCT_DOCS[formData.value.product_type] ?? [],
)

watch(() => formData.value.product_type, () => {
  uploadedFiles.value = requiredDocs.value.map(() => null)
  uploadProgress.value = requiredDocs.value.map(() => '')
})

// Re-apply identity fields when the user switches persona.
watch(() => userStore.current, () => {
  formData.value.validator_id  = defaultValidatorId()
  formData.value.supervisor_id = defaultSupervisorId()
}, { deep: true })

// ------------------------------------------------------------------ //
//  Templates                                                           //
// ------------------------------------------------------------------ //

const templates: Record<string, { label: string; data?: Partial<typeof formData.value>; hint: string }> = {
  custom: {
    label: 'Custom entry',
    hint:  'Edit every field manually.',
  },
  happy_path: {
    label: 'Happy path · matching salary',
    hint:  'Salary on the certificate matches the declared salary. Expected outcome: APPROVE.',
    data: {
      document_ids: 'DMS-00192, DMS-00193',
      applicant_data_json: JSON.stringify({
        name:            'Mohammed Al-Harbi',
        id_number:       '1082345678',
        date_of_birth:   '1985-04-12',
        employer:        'Saudi Aramco',
        declared_salary: 18500.00,
        simah_score:     720,
        t24_account_id:  'T24-ACC-998821',
      }, null, 2),
    },
  },
  salary_mismatch: {
    label: 'Salary mismatch · should HOLD',
    hint:  'Declared 25,000 SAR vs. 18,500 on certificate (>10% deviation). Expected outcome: HOLD.',
    data: {
      document_ids: 'DMS-00192, DMS-00193',
      applicant_data_json: JSON.stringify({
        name:            'Mohammed Al-Harbi',
        id_number:       '1082345678',
        date_of_birth:   '1985-04-12',
        employer:        'Saudi Aramco',
        declared_salary: 25000.00,
        simah_score:     720,
        t24_account_id:  'T24-ACC-998821',
      }, null, 2),
    },
  },
}

const currentHint = computed(() => templates[selectedTemplate.value]?.hint ?? '')

function applyTemplate() {
  const t = templates[selectedTemplate.value]
  if (t?.data) {
    Object.assign(formData.value, t.data)
    // Reset identity fields so the template doesn't overwrite the scoped user.
    formData.value.validator_id  = defaultValidatorId()
    formData.value.supervisor_id = defaultSupervisorId()
  }
}

// ------------------------------------------------------------------ //
//  File reading helper                                                  //
// ------------------------------------------------------------------ //

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload  = () => resolve((reader.result as string).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function onFileChange(idx: number, event: Event) {
  const input = event.target as HTMLInputElement
  uploadedFiles.value[idx] = input.files?.[0] ?? null
  uploadProgress.value[idx] = ''
}

// ------------------------------------------------------------------ //
//  Submit                                                              //
// ------------------------------------------------------------------ //

async function submit() {
  errorMsg.value   = null
  isSubmitting.value = true

  try {
    let docIds: string[]

    if (useFileUpload.value) {
      // Upload each file to DMS first, collect the IDs.
      const slots = requiredDocs.value
      const ids: string[] = []
      for (let i = 0; i < slots.length; i++) {
        const file = uploadedFiles.value[i]
        if (!file) {
          throw new Error(`Please select a file for "${slots[i].label}".`)
        }
        uploadProgress.value[i] = 'Uploading…'
        const base64 = await readAsBase64(file)
        const result = await apiPost<{ document_id: string }>('/api/_demo/dms/upload', {
          document_name:  file.name,
          document_type:  slots[i].type,
          content_base64: base64,
          content_type:   file.type || 'application/octet-stream',
        })
        ids.push(result.document_id)
        uploadProgress.value[i] = `Uploaded → ${result.document_id}`
      }
      docIds = ids
    } else {
      docIds = formData.value.document_ids.split(',').map(s => s.trim()).filter(Boolean)
    }

    const appData = JSON.parse(formData.value.applicant_data_json)
    await apiPost('/api/_demo/submit', {
      product_type:   formData.value.product_type,
      branch_name:    formData.value.branch_name,
      validator_id:   formData.value.validator_id,
      supervisor_id:  formData.value.supervisor_id,
      document_ids:   docIds,
      applicant_data: appData,
    })
    emit('submitted')
    emit('close')
  } catch (e: any) {
    errorMsg.value = e.message ?? 'Submission failed.'
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="modal-card">
      <header class="modal-card-header">
        <div>
          <h3 style="font-size: 15px; font-weight: 600; margin: 0;">Submit Demo Application</h3>
          <p class="muted" style="font-size: 12px; margin-top: 2px;">
            Forwards to the CRM mockup, which publishes a Kafka event into the underwriting pipeline.
          </p>
        </div>
        <button class="btn btn-icon btn-ghost" @click="emit('close')" aria-label="Close">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </header>

      <div class="modal-card-body">
        <!-- Identity banner -->
        <div class="identity-banner">
          <span class="chip chip-dot" :class="userStore.current.role === 'SUPERVISOR' ? 'chip-brand' : 'chip-info'">
            {{ userStore.current.role }}
          </span>
          <span style="font-size: 13px;">Submitting as <strong>{{ userStore.current.display_name }}</strong></span>
          <span class="mono muted" style="font-size: 11px;">{{ userStore.current.user_id }}</span>
        </div>

        <!-- Template hint -->
        <div class="alert alert-info" v-if="currentHint" style="margin-bottom: 18px;">
          <span style="font-weight: 600; margin-right: 6px;">Template:</span>
          {{ currentHint }}
        </div>

        <div class="form-group">
          <label>Use template</label>
          <select v-model="selectedTemplate" @change="applyTemplate">
            <option v-for="(t, k) in templates" :key="k" :value="k">{{ t.label }}</option>
          </select>
        </div>

        <div class="grid-2">
          <div class="form-group">
            <label>Product Type</label>
            <select v-model="formData.product_type">
              <option value="PERSONAL_FINANCE">Personal Finance</option>
              <option value="AUTO_FINANCE" disabled>Auto Finance (no handler yet)</option>
            </select>
          </div>
          <div class="form-group">
            <label>Branch</label>
            <input class="input" v-model="formData.branch_name" type="text" />
          </div>
          <div class="form-group">
            <label>
              Validator ID
              <span v-if="userStore.current.role === 'VALIDATOR'" class="you-badge">you</span>
            </label>
            <input
              class="input"
              v-model="formData.validator_id"
              type="text"
              :readonly="userStore.current.role === 'VALIDATOR'"
              :class="{ 'input-locked': userStore.current.role === 'VALIDATOR' }"
            />
          </div>
          <div class="form-group">
            <label>
              Supervisor ID
              <span v-if="userStore.current.role === 'SUPERVISOR'" class="you-badge">you</span>
            </label>
            <input
              class="input"
              v-model="formData.supervisor_id"
              type="text"
              :readonly="userStore.current.role === 'SUPERVISOR'"
              :class="{ 'input-locked': userStore.current.role === 'SUPERVISOR' }"
            />
          </div>
        </div>

        <!-- Documents section -->
        <div class="docs-section">
          <div class="docs-header">
            <span class="docs-title">Documents</span>
            <button
              class="btn btn-ghost btn-sm"
              type="button"
              @click="useFileUpload = !useFileUpload"
            >
              {{ useFileUpload ? 'Use DMS IDs instead' : 'Upload files instead' }}
            </button>
          </div>

          <!-- File upload mode -->
          <div v-if="useFileUpload" class="upload-slots">
            <div
              v-for="(slot, idx) in requiredDocs"
              :key="slot.type"
              class="upload-slot"
              :class="{ 'upload-slot-done': uploadProgress[idx].startsWith('Uploaded') }"
            >
              <div class="upload-slot-meta">
                <div class="upload-slot-label">{{ slot.label }}</div>
                <div class="muted" style="font-size: 11px; margin-top: 2px;">{{ slot.hint }}</div>
              </div>
              <label class="upload-drop" :class="{ 'has-file': uploadedFiles[idx] }">
                <input
                  type="file"
                  accept="image/*,.pdf"
                  style="display:none"
                  @change="onFileChange(idx, $event)"
                />
                <span v-if="uploadProgress[idx]" class="upload-status">
                  {{ uploadProgress[idx] }}
                </span>
                <span v-else-if="uploadedFiles[idx]" class="upload-filename">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                  {{ uploadedFiles[idx]!.name }}
                </span>
                <span v-else class="upload-placeholder">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
                  Click or drag to upload
                </span>
              </label>
            </div>
          </div>

          <!-- Manual DMS ID mode -->
          <div v-else class="form-group" style="margin-bottom: 0;">
            <label>Document IDs (comma separated)</label>
            <input class="input mono" v-model="formData.document_ids" type="text"
              placeholder="DMS-00192, DMS-00193" />
          </div>
        </div>

        <div class="form-group" style="margin-top: 14px;">
          <label>Applicant Data (JSON)</label>
          <textarea class="input mono" v-model="formData.applicant_data_json" rows="9"></textarea>
        </div>

        <div v-if="errorMsg" class="alert alert-danger">
          <div>
            <div class="alert-title">Submission failed</div>
            <div class="mono" style="margin-top: 4px;">{{ errorMsg }}</div>
          </div>
        </div>
      </div>

      <footer class="modal-card-footer">
        <button class="btn btn-ghost" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary" :disabled="isSubmitting" @click="submit">
          <span v-if="isSubmitting" class="spinner" aria-hidden="true"></span>
          {{ isSubmitting ? 'Submitting…' : 'Submit Application' }}
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.grid-2 {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  margin-top: 10px;
}
.form-group { margin-bottom: 14px; }

.identity-banner {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 14px;
  background: rgba(99,102,241,0.08);
  border: 1px solid rgba(99,102,241,0.2);
  border-radius: var(--r-sm);
  margin-bottom: 18px;
}

.you-badge {
  display: inline-block;
  font-size: 10px; font-weight: 600;
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--brand-soft);
  color: #a5b4fc;
  letter-spacing: .04em;
  margin-left: 6px;
  vertical-align: middle;
}

.input-locked {
  opacity: 0.7;
  cursor: default;
  background: rgba(255,255,255,0.03);
}

/* Documents section */
.docs-section {
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 14px;
  margin-bottom: 14px;
}
.docs-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px;
}
.docs-title { font-size: 13px; font-weight: 600; }

/* Upload slots */
.upload-slots { display: flex; flex-direction: column; gap: 10px; }
.upload-slot {
  display: flex; align-items: flex-start; gap: 14px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  transition: border-color .15s;
}
.upload-slot-done { border-color: rgba(34,197,94,0.4); background: rgba(34,197,94,0.04); }
.upload-slot-meta { flex: 1; min-width: 0; }
.upload-slot-label { font-size: 13px; font-weight: 500; }

.upload-drop {
  flex-shrink: 0;
  width: 200px;
  min-height: 64px;
  display: flex; align-items: center; justify-content: center;
  border: 1.5px dashed var(--border-strong);
  border-radius: var(--r-sm);
  cursor: pointer;
  padding: 10px 12px;
  text-align: center;
  transition: border-color .15s, background .15s;
  font-size: 12px; color: var(--text-3);
}
.upload-drop:hover,
.upload-drop.has-file { border-color: var(--brand); background: var(--brand-soft); color: var(--text-2); }

.upload-placeholder { display: flex; flex-direction: column; align-items: center; gap: 6px; }
.upload-filename { display: flex; align-items: center; gap: 6px; color: var(--text-1); font-weight: 500; }
.upload-status { color: var(--success); font-weight: 500; font-size: 11px; }

.spinner {
  width: 12px; height: 12px;
  border-radius: 999px;
  border: 2px solid rgba(255,255,255,0.4);
  border-top-color: white;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 600px) {
  .grid-2 { grid-template-columns: 1fr; }
  .upload-slot { flex-direction: column; }
  .upload-drop { width: 100%; }
}
</style>
