<script setup>
import { onMounted, onUnmounted, ref } from 'vue';
import { Network, Shapes } from 'lucide-vue-next';
import TripletReview from './components/TripletReview.vue';
import OntologyReview from './components/OntologyReview.vue';
import { api } from './api';

const activeView = ref(location.pathname === '/ontology' ? 'ontology' : 'triplet');
const markdownFiles = ref([]);
const initError = ref('');

function navigate(view) {
  activeView.value = view;
  const path = view === 'ontology' ? '/ontology' : '/';
  if (location.pathname !== path) history.pushState(null, '', path);
}

function onPopState() {
  activeView.value = location.pathname === '/ontology' ? 'ontology' : 'triplet';
}

onMounted(async () => {
  window.addEventListener('popstate', onPopState);
  try {
    const data = await api('/api/init');
    markdownFiles.value = data.markdown_files || [];
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
        <component :is="activeView === 'triplet' ? TripletReview : OntologyReview" :markdown-files="markdownFiles" />
      </KeepAlive>
    </main>
  </div>
</template>
