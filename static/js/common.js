const Api = {
    async request(method, url, body, signal) {
        const options = { method, headers: {}, signal };

        if (body !== undefined) {
            options.headers['Content-Type'] = 'application/json';
            options.body = JSON.stringify(body);
        }

        const response = await fetch(url, options);
        const text = await response.text();

        if (!response.ok) {
            const error = new Error(describeFailure(response, text));
            error.status = response.status;
            throw error;
        }

        return text ? JSON.parse(text) : null;
    },

    get: (url) => Api.request('GET', url),
    post: (url, body) => Api.request('POST', url, body),
    del: (url) => Api.request('DELETE', url)
};

// The API returns plain text for its own errors, but unhandled ones come back as ASP.NET
// ProblemDetails JSON, which is not something to put in front of a person.
function describeFailure(response, text) {
    if (!text)
        return `${response.status} ${response.statusText}`;

    try {
        const problem = JSON.parse(text);
        return problem.detail || problem.title || `${response.status} ${response.statusText}`;
    } catch {
        return text;
    }
}

function reportError(e) {
    toast(e.status === 404
        ? 'This alarm is no longer connected.'
        : e.message, 'err');
}

function toast(message, kind = 'ok') {
    const host = document.getElementById('toast');
    const el = document.createElement('div');
    el.className = kind;

    const text = document.createElement('span');
    text.textContent = message;
    el.appendChild(text);

    // Failures and warnings stay until dismissed. An auto-expiring toast is easy to miss, and a
    // silently failed (or silently non-ideal) write to an alarm panel is the worst thing to be
    // unaware of.
    if (kind === 'err' || kind === 'warn') {
        const close = document.createElement('button');
        close.className = 'small';
        close.textContent = 'Dismiss';
        close.onclick = () => el.remove();
        el.appendChild(close);
    } else {
        setTimeout(() => el.remove(), 3000);
    }

    host.appendChild(el);
}

function modeClass(mode) {
    return 'mode mode-' + String(mode).toLowerCase();
}

const MODE_LABELS = {
    Disarm: 'Disarm',
    AwayArm: 'Away Arm',
    HomeArm: 'Home Arm',
    AwayArmInProgress: 'Away Arm Setting',
    HomeArmInProgress: 'Home Arm Setting',
    Disconnected: 'Disconnected'
};

function modeLabel(mode) {
    return MODE_LABELS[mode] ?? String(mode);
}

// The alarm identifier lives in the query string so a given panel can be linked to directly.
function currentIdentifier() {
    return new URLSearchParams(window.location.search).get('id');
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
}

function formatAge(seconds) {
    if (seconds < 0) return 'in the future';

    const units = [['d', 86400], ['h', 3600], ['m', 60], ['s', 1]];
    const parts = [];

    for (const [suffix, size] of units) {
        const value = Math.floor(seconds / size);
        seconds -= value * size;
        if (value > 0) parts.push(value + suffix);
        if (parts.length === 2) break;
    }

    return parts.length ? parts.join(' ') + ' ago' : 'just now';
}

