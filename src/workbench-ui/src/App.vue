<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import Dashboard from './components/Dashboard.vue'
import SubmitModal from './components/SubmitModal.vue'
import CaseDetails from './components/CaseDetails.vue'
import Notifications from './components/Notifications.vue'

const isSubmitModalOpen = ref(false)
const selectedCaseId = ref<string | null>(null)
const refreshTrigger = ref(0)
const notificationsRef = ref<any>(null)

const openSubmitModal = () => {
  isSubmitModalOpen.value = true
}

const closeSubmitModal = () => {
  isSubmitModalOpen.value = false
}

const onApplicationSubmitted = () => {
  refreshTrigger.value++
}

const viewCase = (id: string) => {
  selectedCaseId.value = id
}

let eventSource: EventSource | null = null

onMounted(() => {
  eventSource = new EventSource('/api/notifications')
  eventSource.addEventListener('case_updated', (event) => {
    const data = JSON.parse(event.data)
    if (notificationsRef.value) {
      notificationsRef.value.addToast(`Application ${data.application_id} finished processing with recommendation: ${data.recommendation}`)
    }
    refreshTrigger.value++
  })
})

onUnmounted(() => {
  if (eventSource) {
    eventSource.close()
  }
})
</script>

<template>
  <div class="app-container">
    <header class="glass header">
      <div>
        <h1 class="title">Reviewer Workbench</h1>
        <p class="subtitle">AI Credit Underwriting Platform</p>
      </div>
      <button class="btn btn-primary" @click="openSubmitModal">
        Submit New Application
      </button>
    </header>

    <main class="main-content">
      <Dashboard 
        :refreshTrigger="refreshTrigger" 
        @viewCase="viewCase" 
      />
    </main>

    <SubmitModal 
      v-if="isSubmitModalOpen" 
      @close="closeSubmitModal" 
      @submitted="onApplicationSubmitted" 
    />

    <CaseDetails 
      v-if="selectedCaseId" 
      :caseId="selectedCaseId" 
      @close="selectedCaseId = null" 
    />

    <Notifications ref="notificationsRef" />
  </div>
</template>

<style scoped>
.app-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.5rem 2rem;
  margin-bottom: 2rem;
}

.title {
  font-size: 1.5rem;
  font-weight: 700;
  margin-bottom: 0.25rem;
  background: linear-gradient(to right, #60a5fa, #a78bfa);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.subtitle {
  color: var(--text-secondary);
  font-size: 0.875rem;
}

.main-content {
  animation: fadeIn 0.5s ease-out;
}
</style>
