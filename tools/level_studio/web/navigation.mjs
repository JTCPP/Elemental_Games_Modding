/** Free camera for Azurik's Z-up world. No level or project data is changed. */
export const NAVIGATION_SPEEDS = Object.freeze({ slow: 0.2, normal: 1, fast: 5 });

export function navigationKeys(locale = 'fr') {
  return locale === 'en' ? ['w', 'q', 's', 'd'] : ['z', 'q', 's', 'd'];
}

export function navigationSpeed(value) {
  const result = Number(NAVIGATION_SPEEDS[value] ?? value);
  return Number.isFinite(result) && result > 0 ? Math.min(100, Math.max(0.01, result)) : 1;
}

function keyName(event, locale = 'fr') {
  // event.key follows the selected physical keyboard layout: AZERTY's Z must
  // remain Z even though browsers report its physical code as KeyW.
  const key = String(event.key || '').toLowerCase();
  if (key === ' ' || key === 'spacebar') return 'space';
  // Windows WebView can expose the US key label even with an AZERTY layout.
  // Keep the translated keys primary and accept these physical equivalents.
  if (locale === 'fr' && key === 'w' && event.code === 'KeyW') return 'z';
  if (key === 'a' && event.code === 'KeyA') return 'q';
  return key;
}

function textControl(element) {
  return !!element && (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(element.tagName)
    || element.isContentEditable || element.closest?.('[contenteditable="true"], [role="textbox"]'));
}

export function createFreeNavigation({
  THREE, camera, orbit, viewport, element = viewport,
  getLocale = () => 'fr', getSpeed = () => 1, getRadius = () => 100,
  getBlocked = () => false, getSensitivity = () => 0.003,
  onEnabledChange = () => {},
  windowTarget = globalThis.window, documentTarget = viewport?.ownerDocument || globalThis.document,
} = {}) {
  if (!THREE || !camera || !orbit || !viewport || !element || !windowTarget || !documentTarget) {
    throw new TypeError('Free navigation needs a camera, orbit controls and a viewport.');
  }

  let enabled = false;
  let looking = false;
  let disposed = false;
  let locale = getLocale() === 'en' ? 'en' : 'fr';
  let yaw = 0;
  let pitch = 0;
  let lookDistance = 1;
  let dragPointer = null;
  let lastX = 0;
  let lastY = 0;
  const held = new Set();
  const forward = new THREE.Vector3();
  const right = new THREE.Vector3();
  const change = new THREE.Vector3();
  const up = new THREE.Vector3(0, 0, 1);
  const listeners = [];
  const lookKeys = ['arrowleft', 'arrowright', 'arrowup', 'arrowdown'];

  const listen = (target, type, callback, options) => {
    target.addEventListener(type, callback, options);
    listeners.push(() => target.removeEventListener(type, callback, options));
  };
  const hasFocus = () => {
    const active = documentTarget.activeElement;
    return !textControl(active) && (active === viewport || active === element || viewport.contains?.(active)
      || active === documentTarget.body || active === documentTarget.documentElement);
  };
  const syncLocale = () => {
    const next = getLocale() === 'en' ? 'en' : 'fr';
    if (next !== locale) { locale = next; reset(); }
  };
  const focus = () => element.focus?.({ preventScroll: true });
  const consume = event => { event.preventDefault?.(); event.stopPropagation?.(); };

  function syncFromCamera() {
    camera.getWorldDirection(forward);
    pitch = Math.asin(THREE.MathUtils.clamp(forward.z, -1, 1));
    yaw = Math.atan2(forward.y, forward.x);
    const radius = Number(getRadius());
    const distance = camera.position.distanceTo(orbit.target);
    lookDistance = Number.isFinite(distance) && distance > 0.00001
      ? distance : Math.max(Number.isFinite(radius) ? radius * 0.1 : 1, 1);
  }

  function stopLooking() {
    const pointer = dragPointer;
    dragPointer = null;
    looking = false;
    if (pointer !== null && element.hasPointerCapture?.(pointer)) element.releasePointerCapture?.(pointer);
    element.style.cursor = '';
  }

  function reset() {
    held.clear();
    stopLooking();
  }

  function clearOrbitMomentum() {
    // OrbitControls keeps damping deltas when disabled. Clear them through its
    // public update method while preserving the exact free-camera pose.
    if (typeof orbit.update !== 'function') return;
    const position = camera.position.clone();
    const quaternion = camera.quaternion.clone();
    const target = orbit.target.clone();
    const damping = orbit.enableDamping;
    orbit.enableDamping = false;
    orbit.update();
    orbit.enableDamping = damping;
    camera.position.copy(position);
    camera.quaternion.copy(quaternion);
    orbit.target.copy(target);
    camera.updateMatrixWorld?.();
  }

  function enable(value, { focusViewport = true } = {}) {
    if (disposed) return;
    reset();
    const next = !!value;
    if (next || enabled) clearOrbitMomentum();
    enabled = next;
    orbit.enabled = !enabled && !getBlocked();
    syncFromCamera();
    if (enabled && focusViewport) focus();
    onEnabledChange(enabled);
  }

  function consumesKey(event) {
    syncLocale();
    if (!enabled || disposed || getBlocked() || !hasFocus() || textControl(event.target)) return false;
    const key = keyName(event, locale);
    // Ctrl/Cmd shortcuts (save, undo, redo, browser commands) keep their meaning.
    if ((event.ctrlKey || event.metaKey) && !['control', 'shift', 'alt'].includes(key)) return false;
    return [...navigationKeys(locale), ...lookKeys, 'space', 'control', 'shift', 'alt'].includes(key);
  }

  function handleKeyDown(event) {
    if (!consumesKey(event)) return false;
    held.add(keyName(event, locale));
    consume(event);
    return true;
  }

  function handleKeyUp(event) {
    const key = keyName(event, locale);
    const wasHeld = held.delete(key);
    if (wasHeld && enabled && hasFocus() && !textControl(event.target)) consume(event);
    return wasHeld;
  }

  function beginLook(event) {
    syncLocale();
    if (disposed || getBlocked() || event.button !== 2) return false;
    // A right drag always means looking from the current position, including
    // immediately after choosing an orbital camera preset.
    if (!enabled) enable(true);
    focus();
    syncFromCamera();
    looking = true;
    dragPointer = event.pointerId ?? 0;
    lastX = Number(event.clientX) || 0;
    lastY = Number(event.clientY) || 0;
    try { element.setPointerCapture?.(dragPointer); } catch { /* WebView may have released this pointer. */ }
    element.style.cursor = 'grabbing';
    consume(event);
    // OrbitControls and TransformControls listen on this same canvas. Their
    // right-button handlers must not pan the camera during a free look drag.
    event.stopImmediatePropagation?.();
    return true;
  }

  function dragLook(event) {
    if (!looking || (event.pointerId ?? 0) !== dragPointer) return false;
    if (!enabled || getBlocked()) { reset(); return false; }
    const x = Number(event.clientX);
    const y = Number(event.clientY);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
    const sensitivity = Number(getSensitivity()) || 0.003;
    yaw -= (x - lastX) * sensitivity;
    // Full horizontal turns, with almost 180 degrees vertically. A tiny pole
    // margin keeps lookAt's Z-up basis stable rather than locking the view down.
    pitch = clampPitch(pitch - (y - lastY) * sensitivity);
    lastX = x;
    lastY = y;
    applyLook();
    consume(event);
    event.stopImmediatePropagation?.();
    return true;
  }

  function clampPitch(value) {
    return THREE.MathUtils.clamp(value, -Math.PI / 2 + 0.0001, Math.PI / 2 - 0.0001);
  }

  function applyLook() {
    forward.set(Math.cos(pitch) * Math.cos(yaw), Math.cos(pitch) * Math.sin(yaw), Math.sin(pitch));
    orbit.target.copy(camera.position).addScaledVector(forward, lookDistance);
    camera.lookAt(orbit.target);
    camera.updateMatrixWorld?.();
  }

  function endLook(event) {
    if (!looking || (event?.pointerId != null && event.pointerId !== dragPointer)) return false;
    stopLooking();
    return true;
  }

  function update(delta) {
    syncLocale();
    if (!enabled || disposed) return;
    orbit.enabled = false;
    if (getBlocked() || !hasFocus()) { reset(); return; }
    const seconds = Math.min(Math.max(Number(delta) || 0, 0), 0.05);
    if (!seconds || !held.size) return;
    const modifier = held.has('alt') ? 0.2 : held.has('shift') ? 5 : 1;
    const horizontal = Number(held.has('arrowleft')) - Number(held.has('arrowright'));
    const vertical = Number(held.has('arrowup')) - Number(held.has('arrowdown'));
    if (horizontal || vertical) {
      // Keyboard look is independent of travel speed and never moves the eye.
      // Arrow keys keep navigation available without a mouse drag gesture.
      const angle = 1.2 * modifier * seconds;
      yaw += horizontal * angle;
      pitch = clampPitch(pitch + vertical * angle);
      applyLook();
    }
    camera.getWorldDirection(forward);
    // This stays well-defined when looking directly up or down.
    right.set(Math.sin(yaw), -Math.cos(yaw), 0);
    change.set(0, 0, 0);
    const [advance, left, back, strafeRight] = navigationKeys(locale);
    if (held.has(advance)) change.add(forward);
    if (held.has(back)) change.sub(forward);
    if (held.has(left)) change.sub(right);
    if (held.has(strafeRight)) change.add(right);
    if (held.has('space')) change.add(up);
    if (held.has('control')) change.sub(up);
    if (!change.lengthSq()) return;
    const radius = Number(getRadius());
    const base = Math.max(Number.isFinite(radius) ? radius * 0.12 : 1, 1);
    change.normalize().multiplyScalar(base * navigationSpeed(getSpeed()) * modifier * seconds);
    camera.position.add(change);
    orbit.target.add(change);
    camera.updateMatrixWorld?.();
  }

  function dispose() {
    if (disposed) return;
    enable(false, { focusViewport: false });
    listeners.splice(0).forEach(remove => remove());
    disposed = true;
  }

  listen(windowTarget, 'keydown', handleKeyDown, { capture: true });
  listen(windowTarget, 'keyup', handleKeyUp, { capture: true });
  listen(windowTarget, 'blur', reset);
  listen(windowTarget, 'languagechange', () => { reset(); syncLocale(); });
  listen(documentTarget, 'focusin', () => { if (!hasFocus()) reset(); });
  listen(documentTarget, 'visibilitychange', () => { if (documentTarget.hidden) reset(); });
  listen(element, 'pointerdown', beginLook, { capture: true });
  listen(element, 'pointermove', dragLook, { capture: true });
  listen(element, 'pointerup', endLook, { capture: true });
  listen(element, 'pointercancel', endLook, { capture: true });
  listen(element, 'lostpointercapture', endLook);
  listen(windowTarget, 'pointerup', endLook);
  listen(element, 'contextmenu', event => { if (enabled) event.preventDefault(); });
  syncFromCamera();

  return {
    enable, update, syncFromCamera, reset, dispose, consumesKey,
    handleKeyDown, handleKeyUp, beginLook, dragLook, endLook,
    get enabled() { return enabled; }, get looking() { return looking; },
  };
}
