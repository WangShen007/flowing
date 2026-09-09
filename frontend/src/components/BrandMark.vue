<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 36 })
</script>

<template>
  <img
    class="brand-mark"
    src="/brand/icon-192.png"
    :width="size"
    :height="size"
    alt=""
    aria-hidden="true"
    draggable="false"
  />
</template>

<style scoped>
.brand-mark { display: inline-block; flex-shrink: 0; object-fit: contain; border-radius: 22%; vertical-align: middle; }
</style>
