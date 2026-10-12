<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
import { Network, Shapes } from 'lucide-vue-next';
import TripletReview from './components/TripletReview.vue';
import OntologyReview from './components/OntologyReview.vue';
import { api } from './api';

const activeView = ref(location.pathname === '/ontology' ? 'ontology' : 'triplet');
const markdownFiles = ref([]);
const modelConfigs = ref([]);
const selectedModelId = ref(localStorage.getItem('workbench.selectedModelId') || 'default');
const initError = ref('');

function navigate(view) {
  activeView.value = view;
  const path = view === 'ontology' ? '/ontology' : '/';
  if (location.pathname !== path) history.pushState(null, '', path);
}

function onPopState() {
  activeView.value = location.pathname === '/ontology' ? 'ontology' : 'triplet';
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
        <button :class="{ active: activeView === 'ontology' }" :aria-current="activeView === 'ontology' ? 'page' : undefined" @click="navigate('ontology')">
          <Shapes :size="17" />本体抽取审核
        </button>
      </nav>
    </header>
    <div v-if="initError" class="global-error" role="alert">{{ initError }}</div>
    <main class="work-area">
      <KeepAlive>
        <component
          :is="activeView === 'triplet' ? TripletReview : OntologyReview"
          :markdown-files="markdownFiles"
          :model-configs="modelConfigs"
          :selected-model-id="selectedModelId"
          @update:selected-model-id="updateSelectedModel"
          @model-created="onModelCreated"
        />
      </KeepAlive>
    </main>
  </div>
</template>
