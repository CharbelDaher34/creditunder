<script setup lang="ts">
import { ref, onMounted } from 'vue'

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'submitted'): void
}>()

const isSubmitting = ref(false)
const selectedTemplate = ref('custom')

const formData = ref({
  product_type: 'PERSONAL_FINANCE',
  branch_name: 'Riyadh Main Branch',
  validator_id: 'CRM-USR-4821',
  supervisor_id: 'CRM-USR-1093',
  document_ids: 'DMS-00192, DMS-00193',
  applicant_data_json: JSON.stringify({
    name: "Mohammed Al-Harbi",
    id_number: "1082345678",
    date_of_birth: "1985-04-12",
    employer: "Saudi Aramco",
    declared_salary: 18500.00,
    simah_score: 720,
    t24_account_id: "T24-ACC-998821"
  }, null, 2)
})

const templates = {
  custom: { label: 'Custom Entry' },
  happy_path: {
    label: 'Happy Path (Match)',
    data: {
      document_ids: 'DMS-00192, DMS-00193',
      applicant_data_json: JSON.stringify({
        name: "Mohammed Al-Harbi",
        id_number: "1082345678",
        date_of_birth: "1985-04-12",
        employer: "Saudi Aramco",
        declared_salary: 18500.00,
        simah_score: 720,
        t24_account_id: "T24-ACC-998821"
      }, null, 2)
    }
  },
  salary_mismatch: {
    label: 'Salary Mismatch (Hold)',
    data: {
      document_ids: 'DMS-00192, DMS-00193',
      applicant_data_json: JSON.stringify({
        name: "Mohammed Al-Harbi",
        id_number: "1082345678",
        date_of_birth: "1985-04-12",
        employer: "Saudi Aramco",
        declared_salary: 25000.00,
        simah_score: 720,
        t24_account_id: "T24-ACC-998821"
      }, null, 2)
    }
  }
}

const applyTemplate = () => {
  if (selectedTemplate.value !== 'custom') {
    const tpl = (templates as any)[selectedTemplate.value].data
    formData.value.document_ids = tpl.document_ids
    formData.value.applicant_data_json = tpl.applicant_data_json
  }
}

const submit = async () => {
  isSubmitting.value = true
  try {
    const docIds = formData.value.document_ids.split(',').map(id => id.trim()).filter(Boolean)
    const appData = JSON.parse(formData.value.applicant_data_json)
    
    const payload = {
      product_type: formData.value.product_type,
      branch_name: formData.value.branch_name,
      validator_id: formData.value.validator_id,
      supervisor_id: formData.value.supervisor_id,
      document_ids: docIds,
      applicant_data: appData
    }

    const res = await fetch('/api/cases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    
    if (res.ok) {
      emit('submitted')
      emit('close')
    } else {
      alert('Failed to submit application')
    }
  } catch (e) {
    console.error(e)
    alert('Invalid JSON or submission error')
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-content glass">
      <div class="modal-header">
        <h3>Submit New Application</h3>
        <button class="close-btn" @click="emit('close')">&times;</button>
      </div>
      
      <div class="modal-body">
        <div class="form-group template-selector">
          <label>Use Template</label>
          <select v-model="selectedTemplate" @change="applyTemplate">
            <option v-for="(tpl, key) in templates" :key="key" :value="key">
              {{ tpl.label }}
            </option>
          </select>
        </div>
        
        <div class="grid-2">
          <div class="form-group">
            <label>Product Type</label>
            <select v-model="formData.product_type">
              <option value="PERSONAL_FINANCE">Personal Finance</option>
              <option value="AUTO_FINANCE">Auto Finance</option>
            </select>
          </div>
          <div class="form-group">
            <label>Branch Name</label>
            <input v-model="formData.branch_name" type="text" />
          </div>
          <div class="form-group">
            <label>Validator ID</label>
            <input v-model="formData.validator_id" type="text" />
          </div>
          <div class="form-group">
            <label>Supervisor ID</label>
            <input v-model="formData.supervisor_id" type="text" />
          </div>
        </div>

        <div class="form-group">
          <label>Document IDs (comma separated)</label>
          <input v-model="formData.document_ids" type="text" />
        </div>

        <div class="form-group">
          <label>Applicant Data (JSON)</label>
          <textarea v-model="formData.applicant_data_json" rows="8" class="font-mono"></textarea>
        </div>
      </div>
      
      <div class="modal-footer">
        <button class="btn btn-secondary" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary ml-2" :disabled="isSubmitting" @click="submit">
          {{ isSubmitting ? 'Submitting...' : 'Submit Application' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
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
.modal-footer {
  padding: 1.5rem;
  border-top: 1px solid var(--surface-border);
  display: flex;
  justify-content: flex-end;
}
.grid-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}
.ml-2 {
  margin-left: 0.5rem;
}
.template-selector {
  margin-bottom: 1.5rem;
  padding-bottom: 1.5rem;
  border-bottom: 1px dashed var(--surface-border);
}
.font-mono {
  font-family: monospace;
  font-size: 0.875rem;
}
</style>
