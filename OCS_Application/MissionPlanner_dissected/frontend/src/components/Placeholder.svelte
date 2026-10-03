<script lang="ts">
  let { text }: { text: string } = $props();
</script>

<div class="placeholder">{text}</div>

<style>
  .placeholder {
    height: 100%;
    min-height: 3rem;
    display: grid;
    place-items: center;
    text-align: center;
    color: var(--muted);
    border: 1px dashed var(--border);
    border-radius: 4px;
    font-size: 0.85rem;
  }
</style>
