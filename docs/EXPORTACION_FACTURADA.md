# Exportar una empresa hacia FacturADA

2026-10-29. En Desk → Company → abrir una empresa guardada → Migración →
**Exportar para FacturADA**. Solo System Manager/Administrator, con permiso de
lectura sobre Company. El ZIP contiene exclusivamente sus datos y los métodos de
pago compartidos que sus documentos referencian. No modifica ni elimina origen.

Incluye clientes/categorías/productos/stock y registros originales de órdenes,
subcuentas, caja, facturas, NC y movimientos. Adjuntos locales PDF/XML/imágenes;
no descarga URLs remotas ni incluye firma/credenciales. Las omisiones remotas se
advierten en el manifiesto y en la revisión del destino. Conserva referencias de
usuarios; no es una exportación de cuentas o permisos.

Antes del corte, cerrar turnos y resolver órdenes y documentos pendientes. Dejar
de operar esa empresa hasta completar el traslado y no volver a facturar en el
sistema antiguo después; las demás empresas siguen independientes. El exportador
no congela automáticamente la operación.

Formato v1 `facturada-restaurante-transfer`: manifest.json, data.json y adjuntos
por hash. Hasta 50 MB sin comprimir / 5000 miembros; Frappe puede limitar a menos
la carga en destino. Guardar el ZIP de forma privada: contiene datos personales.

Actualizar transfer.py y el Company JS en el sitio fuente, limpiar cache y
recargar Desk. No se requiere instalar Core/Lite en el origen ni migrar esquemas
para el botón de exportación. Reiniciar el proceso si mantiene Python antiguo.

El usuario prepara el negocio destino y carga el ZIP en el Desk de FacturADA:
FacturADA Business → negocio → Migración → Importar desde Restaurante. La guía del importador está en
`facturada_lite/docs/TRANSFERENCIA_RESTAURANTE.md`.
