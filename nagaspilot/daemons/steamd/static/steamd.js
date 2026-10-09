/**
 * SteamD Web Dashboard
 *
 * UDP-only architecture — browsers cannot receive raw UDP H264, so this
 * client is a status monitor + emergency control interface.
 *
 * Features:
 *   - Polls /status for telemetry, engagement, link health
 *   - Keyboard/gamepad emergency disengage
 *   - HTTP POST /control for remote commands (token auth)
 */

class SteamDClient {
    constructor() {
        this.engaged = false;
        this.connected = false;
        this.gamepadIndex = null;
        this._pollInterval = null;
        this._keys = {};

        const urlParams = new URLSearchParams(window.location.search);
        this.authToken = urlParams.get('token') || localStorage.getItem('steamd_auth_token') || '';

        this.init();
    }

    async init() {
        console.log('SteamD dashboard initializing...');
        this.setupKeyboard();
        this.setupGamepad();
        this.startPolling();
    }

    /** Poll /status every 500 ms for telemetry. */
    startPolling() {
        this._pollStatus();
        this._pollInterval = setInterval(() => this._pollStatus(), 500);
    }

    async _pollStatus() {
        try {
            const res = await fetch('/status');
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const data = await res.json();
            this._updateUI(data);
        } catch (err) {
            this.connected = false;
            this._updateConnectionUI(false, 'Server unreachable');
        }
    }

    _updateUI(data) {
        this.connected = data.steamd_connected;
        this.engaged = data.vehicle_engaged;
        this._updateConnectionUI(this.connected,
            data.udp_input_alive ? 'UDP link OK' : 'UDP link down');

        const setText = (id, text) => {
            const el = document.getElementById(id);
            if (el) el.textContent = text;
        };
        setText('speed', (data.v_ego_ms * 3.6).toFixed(1));
        setText('steering', data.steering_angle_deg.toFixed(1));
        setText('mode', data.vehicle_engaged ? 'Auto (VR)' : 'Manual');
        setText('gear', data.gear);
        setText('gas', (data.gas * 100).toFixed(0));
        setText('brake', (data.brake * 100).toFixed(0));
        setText('udp-last', data.udp_last_ms >= 0 ? data.udp_last_ms + ' ms' : 'N/A');
        setText('stream-target', data.stream_target || 'Disabled');
        setText('view-mode', data.view_mode);

        const ind = document.getElementById('conn-indicator');
        if (ind) {
            ind.className = 'indicator ' + (data.udp_input_alive ? 'connected' : 'disconnected');
        }

        // Blinkers
        const leftBlink = document.getElementById('left-blinker');
        const rightBlink = document.getElementById('right-blinker');
        if (leftBlink) leftBlink.style.opacity = data.left_blinker ? '1' : '0.2';
        if (rightBlink) rightBlink.style.opacity = data.right_blinker ? '1' : '0.2';
    }

    _updateConnectionUI(connected, text) {
        const status = document.getElementById('conn-status');
        if (status) status.textContent = text || (connected ? 'Connected' : 'Disconnected');
    }

    setupKeyboard() {
        document.addEventListener('keydown', (e) => {
            this._keys[e.key] = true;
            if (e.key === ' ' || e.key === 'Escape') {
                e.preventDefault();
                this.disengage();
            }
        });
        document.addEventListener('keyup', (e) => {
            this._keys[e.key] = false;
        });
    }

    setupGamepad() {
        window.addEventListener('gamepadconnected', (e) => {
            console.log('Gamepad connected:', e.gamepad.id);
            this.gamepadIndex = e.gamepad.index;
        });
        window.addEventListener('gamepaddisconnected', (e) => {
            console.log('Gamepad disconnected:', e.gamepad.id);
            this.gamepadIndex = null;
        });
        // Gamepad read loop at 20 Hz
        setInterval(() => this._readGamepad(), 50);
    }

    _readGamepad() {
        if (this.gamepadIndex === null) return;
        const gp = navigator.getGamepads()[this.gamepadIndex];
        if (!gp) return;

        // B button (idx 1) or Menu (idx 9) = disengage
        if (gp.buttons[1]?.pressed || gp.buttons[9]?.pressed) {
            this.disengage();
        }
    }

    async _postControl(payload) {
        const headers = { 'Content-Type': 'application/json' };
        if (this.authToken) headers['Authorization'] = 'Bearer ' + this.authToken;
        try {
            const res = await fetch('/control', {
                method: 'POST',
                headers,
                body: JSON.stringify(payload)
            });
            return res.ok;
        } catch (err) {
            console.error('Control POST failed:', err);
            return false;
        }
    }

    engage() {
        console.log('Engage requested from web UI (no-op — use VR controller)');
        // Engagement is handled via UDP from headset; web UI is monitor-only
    }

    async disengage() {
        console.log('Emergency disengage via web UI');
        await this._postControl({ disengage: true });
    }

    destroy() {
        if (this._pollInterval) {
            clearInterval(this._pollInterval);
            this._pollInterval = null;
        }
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    window.steamdClient = new SteamDClient();
});

// Handle page unload
window.addEventListener('beforeunload', () => {
    if (window.steamdClient) {
        window.steamdClient.destroy();
    }
});
