// Keep upstream bundles unchanged. Load the renderer before the viewer that
// consumes globalThis.pdfjsLib, and report startup failures to the host.
function report(error) {
  console.error('Reader startup failed:', error);
  parent.postMessage({ type: 'raz-reader-startup-error', url: location.href }, location.origin);
}
window.addEventListener('unhandledrejection', event => report(event.reason));
window.addEventListener('error', event => report(event.error || event.message));
try {
  await import('/vendor/pdfjs/build/pdf.mjs');
  await import('/vendor/pdfjs/web/viewer.mjs');
} catch (error) { report(error); }
