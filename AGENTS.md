# Restaurante BMARC — aplicación anterior

Aplicación heredada. No introducir operación nueva aquí cuando corresponda a
Core/Lite/Restaurante. La excepción autorizada es la exportación de una empresa
para migrar hacia FacturADA.

`restaurante_app/transfer.py` y el botón de Company producen un ZIP v1 de solo
lectura. Solo System Manager/Administrator y permiso de lectura de la Company.
Todo registro de negocio se filtra por company_id; payments es compartido y solo
se incluyen los métodos referenciados. Mantener la exclusión de secretos/firma,
las restricciones de adjuntos locales, límites y el manifiesto de integridad.
No emitir, consultar SRI, renovar planes ni modificar datos como efecto de exportar.

Guía: docs/EXPORTACION_FACTURADA.md. Pruebas puras del exportador/parser en
facturada_lite/api/test_legacy_transfer.py; no instalar esta aplicación en el sitio
de pruebas nuevo para probar la exportación ni tocar datos reales por rutina.
Actualizar ESTADO.md y la guía si cambia el contrato o comportamiento.
