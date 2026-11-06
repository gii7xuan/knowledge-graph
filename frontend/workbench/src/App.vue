<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { Network, Shapes, Activity } from 'lucide-vue-next';
import TripletReview from './components/TripletReview.vue';
import OntologyReview from './components/OntologyReview.vue';
import ReviewMonitor from './components/ReviewMonitor.vue';
import { api } from './api';

const viewForPath = () => location.pathname === '/review-monitor' ? 'monitor' : location.pathname === '/ontology' ? 'ontology' : 'triplet';
const readMonitorSelection = () => Object.fromEntries([...new URLSearchParams(location.search)].filter(([key]) => ['source_id', 'batch_id', 'candidate_id', 'audit_id', 'round_id'].includes(key)));
const activeView = ref(viewForPath());
const monitorSelection = ref(readMonitorSelection());
const views = { triplet: TripletReview, ontology: OntologyReview, monitor: ReviewMonitor };
const markdownFiles = ref([]);
const modelConfigs = ref([]);
const selectedModelId = ref(localStorage.getItem('workbench.selectedModelId') || 'default');
const initError = ref('');

const componentProps = computed(() => activeView.value === 'monitor' ? { selection: monitorSelection.value } : {
  markdownFiles: markdownFiles.value, modelConfigs: modelConfigs.value, selectedModelId: selectedModelId.value
});

function navigate(view, selection = null) {
  activeView.value = view;
  const path = view === 'monitor' ? '/review-monitor' : view === 'ontology' ? '/ontology' : '/';
  const target = path + (selection ? '?' + new URLSearchParams(selection) : '');
  if (location.pathname + location.search !== target) history.pushState(null, '', target);
}

function onPopState() {
  activeView.value = viewForPath();
  if (activeView.value === 'monitor') monitorSelection.value = readMonitorSelection();
}

function openMonitor(selection) {
  monitorSelection.value = { source_id: 'production', ...selection };
  navigate('monitor', monitorSelection.value);
}

function chooseAvailableModel(preferredId) {
  const ids = new Set(modelConfigs.value.map(item => item.id));
  selectedModelId.value = ids.has(preferredId) ? preferredId : modelConfigs.value[0]?.id || 'default';
  localStorage.setItem('workbench.selectedModelId', selectedModelId.value);
}

async function refreshModels(preferredId = selectedModelId.value) {
  const data = await api('/api/models');
  modelConfigs.value = data.models || [];
  chooseAvailableModel(preferredId);
}

async function onModelCreated(model) {
  await refreshModels(model?.id || selectedModelId.value);
}

function updateSelectedModel(id) {
  selectedModelId.value = id || 'default';
  localStorage.setItem('workbench.selectedModelId', selectedModelId.value);
}

onMounted(async () => {
  window.addEventListener('popstate', onPopState);
  try {
    const data = await api('/api/init');
    markdownFiles.value = data.markdown_files || [];
    modelConfigs.value = data.model_configs || [];
    chooseAvailableModel(selectedModelId.value);
  } catch (error) {
    initError.value = error.message;
  }
});

onUnmounted(() => window.removeEventListener('popstate', onPopState));
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand">知识图谱审核工作台</div>
      <nav class="topnav" aria-label="一级导航">
        <button :class="{ active: activeView === 'triplet' }" :aria-current="activeView === 'triplet' ? 'page' : undefined" @click="navigate('triplet')">
          <Network :size="17" />三元组抽取审核
        </button>
        <button :class="{ active: activeView === 'monitor' }" :aria-current="activeView === 'monitor' ? 'page' : undefined" @click="navigate('monitor')">
          <Activity :size="17" />Agent 审核监控
        </button>
        <button :class="{ active: activeView === 'ontology' }" :aria-current="activeView === 'ontology' ? 'page' : undefined" @click="navigate('ontology')">
          <Shapes :size="17" />本体抽取审核
        </button>
      </nav>
    </header>
    <div v-if="initError" class="global-error" role="alert">{{ initError }}</div>
    <main class="work-area">
      <KeepAlive>
        <component
          :is="views[activeView]"
          v-bind="componentProps"
          @update:selected-model-id="updateSelectedModel"
          @model-created="onModelCreated"
          @open-monitor="openMonitor"
        />
      </KeepAlive>
    </main>
  </div>
</template>
