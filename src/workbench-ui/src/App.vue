<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import Dashboard from './components/Dashboard.vue'
import SubmitModal from './components/SubmitModal.vue'
import CaseDetails from './components/CaseDetails.vue'
import Notifications from './components/Notifications.vue'
import UserSwitcher from './components/UserSwitcher.vue'
import { eventSource } from './api'
import { userStore } from './userStore'

const isSubmitModalOpen = ref(false)
const selectedCaseId = ref<string | null>(null)
const refreshTrigger = ref(0)
const notificationsRef = ref<any>(null)

const openSubmitModal = () => { isSubmitModalOpen.value = true }
const closeSubmitModal = () => { isSubmitModalOpen.value = false }
const onApplicationSubmitted = () => { refreshTrigger.value++ }
const viewCase = (id: string) => { selectedCaseId.value = id }

let es: EventSource | null = null

async function connectStream() {
  if (es) { es.close(); es = null }
  try {
    es = await eventSource('/api/_demo/notifications')
  } catch {
    // Token mint failed (server not yet up or user not authenticated).
    // The watch will retry on next identity change; the user can also
    // manually trigger a refresh by switching user in the UserSwitcher.
    return
  }
  es.addEventListener('case_updated', (event: MessageEvent) => {
    const data = JSON.parse(event.data)
    const tone = data.status === 'COMPLETED' ? 'success'
              : data.status === 'FAILED' ? 'danger'
              : data.status === 'MANUAL_INTERVENTION_REQUIRED' ? 'warning'
              : 'info'
    if (notificationsRef.value) {
      const verdict = data.recommendation ? `Recommendation: ${data.recommendation}` : data.status
      notificationsRef.value.addToast(`${data.application_id} · ${verdict}`, tone)
    }
    refreshTrigger.value++
  })
  es.onerror = () => { /* the browser auto-reconnects */ }
}

onMounted(() => { connectStream() })
onUnmounted(() => { if (es) es.close() })

// Reconnect SSE when the user switches identity, since the stream is scoped.
watch(() => userStore.current, () => { connectStream(); refreshTrigger.value++ }, { deep: true })
</script>

<template>
  <div class="shell">
    <header class="appbar">
      <div class="brand">
        <div class="brand-mark">
          <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
            <defs>
              <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#a78bfa"/>
                <stop offset="100%" stop-color="#38bdf8"/>
              </linearGradient>
            </defs>
            <path fill="url(#lg)" d="M6 6h20v4H6zM6 14h14v4H6zM6 22h20v4H6z"/>
          </svg>
        </div>
        <div class="brand-text">
          <div class="brand-title">Reviewer Workbench</div>
          <div class="brand-sub">AI Credit Underwriting · Alinma Bank</div>
        </div>
      </div>

      <div class="appbar-right">
        <UserSwitcher />
        <button class="btn btn-primary" @click="openSubmitModal">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>
          Submit Application
        </button>
      </div>
    </header>

    <main class="main">
      <Dashboard
        :refresh-trigger="refreshTrigger"
        @view-case="viewCase"
      />
    </main>

    <SubmitModal
      v-if="isSubmitModalOpen"
      @close="closeSubmitModal"
      @submitted="onApplicationSubmitted"
    />

    <CaseDetails
      v-if="selectedCaseId"
      :case-id="selectedCaseId"
      @close="selectedCaseId = null"
    />

    <Notifications ref="notificationsRef" />
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.appbar {
  position: sticky; top: 0; z-index: 30;
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 28px;
  background: rgba(11, 16, 32, 0.78);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--border);
}

.brand { display: flex; align-items: center; gap: 12px; }
.brand-mark {
  width: 38px; height: 38px; border-radius: 10px;
  display: grid; place-items: center;
  background: linear-gradient(135deg, rgba(99,102,241,0.25), rgba(56,189,248,0.18));
  border: 1px solid var(--border-strong);
}
.brand-title {
  font-size: 15px; font-weight: 600; letter-spacing: .01em;
}
.brand-sub {
  font-size: 11px; color: var(--text-3); margin-top: 1px;
}

.appbar-right {
  display: flex; align-items: center; gap: 12px;
}

.main {
  flex: 1;
  padding: 28px;
  max-width: 1400px;
  width: 100%;
  margin: 0 auto;
}

@media (max-width: 720px) {
  .appbar { padding: 12px 16px; }
  .brand-sub { display: none; }
  .main { padding: 16px; }
}
</style>
