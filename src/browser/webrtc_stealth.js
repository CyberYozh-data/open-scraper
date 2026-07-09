// WebRTC leak protection, injected via context.add_init_script before any page
// script runs. The Chromium launch flags (--webrtc-ip-handling-policy=...) are
// not always effective in headless mode (verified: without this the real IP
// still leaks via ICE), so WebRTC is also neutralised at the JS level here.
//
// RTCPeerConnection is kept PRESENT and NATIVE-LOOKING — a deleted API
// (`typeof RTCPeerConnection === 'undefined'`) and a hand-rolled wrapper
// (non-native `toString()` / missing `generateCertificate`) are each their own
// automation tell. The constructor is fronted by a Proxy that forwards every
// static member / `toString` / `length` / `instanceof` to the native impl,
// while each instance drops the ICE candidates (`typ host|srflx|prflx`) that
// would expose the real host/server IP. Normal event semantics are preserved:
// add/removeEventListener symmetry, a single-slot `onicecandidate`, and the
// end-of-candidates null event.
(() => {
  const Orig = window.RTCPeerConnection || window.webkitRTCPeerConnection;
  if (Orig) {
    const LEAKY = /\btyp\s+(host|srflx|prflx)\b/i;
    const isLeaky = (c) => !!c && LEAKY.test(c);
    const patch = (pc) => {
      const wrap = new WeakMap();
      const guardFor = (cb) => {
        let g = wrap.get(cb);
        if (!g) {
          g = (ev) => {
            if (ev && ev.candidate && isLeaky(ev.candidate.candidate)) return;
            return cb.call(pc, ev);
          };
          wrap.set(cb, g);
        }
        return g;
      };
      const rawAdd = EventTarget.prototype.addEventListener.bind(pc);
      const rawRemove = EventTarget.prototype.removeEventListener.bind(pc);
      Object.defineProperty(pc, 'addEventListener', {
        configurable: true, writable: true,
        value(type, cb, ...rest) {
          if (type === 'icecandidate' && typeof cb === 'function') {
            return rawAdd(type, guardFor(cb), ...rest);
          }
          return rawAdd(type, cb, ...rest);
        },
      });
      Object.defineProperty(pc, 'removeEventListener', {
        configurable: true, writable: true,
        value(type, cb, ...rest) {
          if (type === 'icecandidate' && typeof cb === 'function' && wrap.has(cb)) {
            return rawRemove(type, wrap.get(cb), ...rest);
          }
          return rawRemove(type, cb, ...rest);
        },
      });
      let slot = null, slotWrapped = null;
      Object.defineProperty(pc, 'onicecandidate', {
        configurable: true, enumerable: true,
        get() { return slot; },
        set(cb) {
          if (slotWrapped) rawRemove('icecandidate', slotWrapped);
          slot = (typeof cb === 'function') ? cb : null;
          slotWrapped = slot ? guardFor(slot) : null;
          if (slotWrapped) rawAdd('icecandidate', slotWrapped);
        },
      });
      return pc;
    };
    const handler = {
      construct(target, args) { return patch(Reflect.construct(target, args, target)); },
      get(target, prop, recv) {
        // Keep Function.prototype.toString native-looking ([native code]);
        // forward everything else unchanged so static members keep their native
        // identity and .name (binding them would expose "bound
        // generateCertificate" etc.).
        if (prop === 'toString') return target.toString.bind(target);
        return Reflect.get(target, prop, recv);
      },
    };
    const Proxied = new Proxy(Orig, handler);
    try { Orig.prototype.constructor = Proxied; } catch (_) {}
    try { window.RTCPeerConnection = Proxied; } catch (_) {}
    try { if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = Proxied; } catch (_) {}
  }
  if (navigator.mediaDevices) {
    try {
      navigator.mediaDevices.getUserMedia = () =>
        Promise.reject(new DOMException('Permission denied', 'NotAllowedError'));
    } catch (_) {}
  }
})();
