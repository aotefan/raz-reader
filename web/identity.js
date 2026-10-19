// Every private request is bound to the identity that created it. A cookie may
// change in another tab while an upload or an offline progress write is waiting.
let scope = 'local';
export function setIdentity(value) { scope = value || 'local'; }
export function identityScope() { return scope; }
export function scopeHeaders(value = scope) { return { 'X-Reader-Scope': value }; }
