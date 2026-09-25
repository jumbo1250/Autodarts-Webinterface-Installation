// Läuft in einem versteckten HTML-Dokument – hat DOM-Zugriff und damit echtes WebRTC.
// Service Worker (background.js) kann kein RTCPeerConnection nutzen, dieses Dokument schon.

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.target !== 'offscreen') return;
  if (msg.type === 'GET_LOCAL_IPS') {
    getLocalIps().then(ips => sendResponse({ ips }));
    return true;
  }
});

function getLocalIps() {
  return new Promise(resolve => {
    const found = new Set();
    let pc;
    try {
      pc = new RTCPeerConnection({ iceServers: [] });
    } catch {
      return resolve(null);
    }

    pc.createDataChannel('');

    function finish() {
      const ips = [...found].filter(ip =>
        /^(\d{1,3}\.){3}\d{1,3}$/.test(ip) &&
        !ip.startsWith('169.254') &&
        !ip.startsWith('127.')
      );
      resolve(ips.length ? ips : null);
    }

    pc.onicecandidate = e => {
      if (!e.candidate) { try { pc.close(); } catch {} finish(); return; }
      const m = e.candidate.candidate.match(/(\d{1,3}(?:\.\d{1,3}){3})/);
      if (m) found.add(m[1]);
    };

    pc.createOffer()
      .then(o => pc.setLocalDescription(o))
      .catch(() => resolve(null));

    setTimeout(() => { try { pc.close(); } catch {} finish(); }, 3000);
  });
}
