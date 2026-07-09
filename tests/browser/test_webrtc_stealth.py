"""WebRTC leak-protection init script keeps the API present and native-looking.

The script must neither delete RTCPeerConnection (a `typeof === 'undefined'`
tell) nor expose a hand-rolled wrapper (a non-native `toString()` / missing
`generateCertificate` tell). It fronts the native constructor with a Proxy and
filters leaky ICE candidates per instance.
"""
from __future__ import annotations

import pytest

from src.browser.runner import PlaywrightRunner


@pytest.mark.asyncio
async def test_webrtc_api_present_and_native_looking():
    runner = PlaywrightRunner(headless=True, block_assets=False, timeout_ms=30000)
    await runner.start()
    try:
        ctx = await runner._new_context(device="desktop", proxy=None, headers=None)
        page = await ctx.new_page()
        await page.goto("about:blank")
        res = await page.evaluate(
            """() => {
                const R = window.RTCPeerConnection;
                const out = {
                  type: typeof R,
                  nativeToString: ('' + R).includes('[native code]'),
                  hasGenerateCertificate: typeof (R && R.generateCertificate),
                  // static members must keep native .name and stable identity —
                  // binding them would expose "bound generateCertificate" and a
                  // different object on every access.
                  genCertName: R && R.generateCertificate && R.generateCertificate.name,
                  genCertStableIdentity: R && R.generateCertificate === R.generateCertificate,
                  name: R && R.name,
                };
                try {
                  const pc = new R({ iceServers: [] });
                  out.instanceOf = pc instanceof R;
                  out.ctorIdentity = pc.constructor === window.RTCPeerConnection;
                  const cb = () => {};
                  pc.onicecandidate = cb;
                  out.slotReadBack = pc.onicecandidate === cb;   // single-slot identity
                  pc.onicecandidate = null;
                  out.slotCleared = pc.onicecandidate;            // null after clear

                  // Leaky (typ host/srflx/prflx) candidates are dropped; relay and
                  // the end-of-candidates null event pass through.
                  const seen = [];
                  pc.onicecandidate = (ev) => seen.push(ev && ev.candidate ? ev.candidate.candidate : null);
                  const fire = (c) => {
                    const ev = new Event('icecandidate');
                    ev.candidate = c === null ? null : { candidate: c };
                    pc.dispatchEvent(ev);
                  };
                  fire('candidate:1 1 udp 99 192.168.1.5 5000 typ host');   // dropped
                  fire('candidate:2 1 udp 99 1.2.3.4 5000 typ relay');      // kept
                  fire(null);                                               // kept (end)
                  out.seen = seen;
                  pc.close();
                } catch (e) {
                  out.err = e.name + ': ' + e.message;
                }
                return out;
            }"""
        )
        await ctx.close()
    finally:
        await runner.stop()

    assert res.get("err") is None, res.get("err")
    assert res["type"] == "function"                     # present, not deleted
    assert res["nativeToString"] is True                 # not a hand-rolled function body
    assert res["hasGenerateCertificate"] == "function"   # static members forwarded
    assert res["genCertName"] == "generateCertificate"   # native name, not "bound …"
    assert res["genCertStableIdentity"] is True          # same object across reads
    assert res["name"] == "RTCPeerConnection"
    assert res["instanceOf"] is True                     # Proxy preserves instanceof
    assert res["ctorIdentity"] is True                   # pc.constructor === window.RTCPeerConnection
    assert res["slotReadBack"] is True                   # onicecandidate single-slot read-back
    assert res["slotCleared"] is None                    # assigning null detaches
    # host candidate dropped; relay + end-of-candidates(null) delivered.
    assert res["seen"] == ["candidate:2 1 udp 99 1.2.3.4 5000 typ relay", None]
