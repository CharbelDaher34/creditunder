<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'

const props = defineProps<{
  refreshTrigger: number
}>()

const emit = defineEmits<{
  (e: 'viewCase', id: string): void
}>()

const cases = ref<any[]>([])
const loading = ref(true)

const fetchCases = async () => {
  loading.value = true
  try {
    const res = await fetch('/api/cases')
    cases.value = await res.json()
  } catch (e) {
    console.error('Failed to fetch cases', e)
  } finally {
    loading.value = false
  }
}

watch(() => props.refreshTrigger, () => {
  fetchCases()
})

onMounted(() => {
  fetchCases()
})

const getStatusBadgeClass = (status: string) => {
  if (status === 'COMPLETED') return 'badge-success'
  if (status === 'FAILED') return 'badge-danger'
  return 'badge-info'
}

const getRecBadgeClass = (rec: string) => {
  if (rec === 'APPROVE') return 'badge-success'
  if (rec === 'HOLD') return 'badge-warning'
  if (rec === 'DECLINE') return 'badge-danger'
  return ''
}
</script>

<template>
  <div class="glass p-4 dashboard-container">
    <h2 class="section-title">Recent Cases</h2>
    
    <div v-if="loading" class="loading-state">
      Loading cases...
    </div>
    
    <div v-else class="table-container">
      <table>
        <thead>
          <tr>
            <th>Application ID</th>
            <th>Product</th>
            <th>Branch</th>
            <th>Status</th>
            <th>Recommendation</th>
            <th>Submitted</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in cases" :key="c.id">
            <td class="font-medium">{{ c.application_id }}</td>
            <td>{{ c.product_type }}</td>
            <td>{{ c.branch_name }}</td>
            <td>
              <span class="badge" :class="getStatusBadgeClass(c.status)">
                {{ c.status }}
              </span>
            </td>
            <td>
              <span v-if="c.recommendation" class="badge" :class="getRecBadgeClass(c.recommendation)">
                {{ c.recommendation }}
              </span>
              <span v-else class="text-secondary">-</span>
            </td>
            <td>{{ new Date(c.created_at).toLocaleString() }}</td>
            <td>
              <button class="btn btn-secondary btn-sm" @click="emit('viewCase', c.application_id)">
                View Details
              </button>
            </td>
          </tr>
          <tr v-if="cases.length === 0">
            <td colspan="7" class="text-center text-secondary py-8">
              No cases found.
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.dashboard-container {
  padding: 1.5rem;
}
.section-title {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 1.5rem;
}
.loading-state {
  text-align: center;
  padding: 3rem;
  color: var(--text-secondary);
}
.btn-sm {
  padding: 0.25rem 0.75rem;
  font-size: 0.875rem;
}
.font-medium {
  font-weight: 500;
}
.text-secondary {
  color: var(--text-secondary);
}
.text-center {
  text-align: center;
}
.py-8 {
  padding-top: 2rem;
  padding-bottom: 2rem;
}
</style>
