# Estado de Restaurante BMARC (heredado)

Actualizado: 2026-10-29.

## Hecho

- Guía de destino corregida: importación administrativa exclusivamente en Desk.
  Exportador y datos fuente no cambiaron en esta revisión.

- Exportación de una sola Company a ZIP mediante botón Migración → Exportar para
  FacturADA. Datos maestros, stock e históricos; adjuntos locales y referencias de
  contadores; permisos de System Manager/Administrator, sin secretos ni firma.
- Aislamiento por company_id y métodos compartidos solo referenciados. Límites de
  50 MB sin comprimir/5000 miembros, hashes y advertencias por adjuntos remotos.
- Sin cambio de operación comercial ni escrituras al origen. Guía de exportación
  y AGENTS.md creados. Pruebas puras se ejecutan desde Lite sin instalar Legacy.

## Pendiente

- Publicar el exportador en el sitio origen real y validar una Company elegida por
  el usuario. No se exportó una empresa real en esta sesión.
- Coordinar el corte de esa empresa: la herramienta no la congela automáticamente.
- ZIPs mayores necesitan exportación por lotes, no implementada.
