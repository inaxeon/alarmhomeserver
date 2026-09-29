let id = currentIdentifier();
let maxDeviceNameLength = 10;
let currentMode = null;
let currentType = null;
let isPollingAlarm = false;
let shouldAutoRefreshOpenDcs = false;
let canViewMedia = false;
let canControlDevices = false;
let canViewHistory = false;
let isModeChanging = false;

const $ = (sel) => document.querySelector(sel);

// Disables a button and shows a spinner for the duration of a request so a slow panel can't be double-submitted.
async function guard(button, action) {
    if (!button) return action();

    const wasDisabled = button.disabled;
    button.disabled = true;
    button.classList.add('busy');

    let spinner = null;
    if (button.tagName === 'BUTTON' || button.tagName === 'A') {
        spinner = document.createElement('span');
        spinner.className = 'spinner';
        button.insertBefore(spinner, button.firstChild);
    }

    try {
        await action();
    } catch (e) {
        reportError(e);
    } finally {
        if (spinner) {
            spinner.remove();
        }
        button.classList.remove('busy');
        if (button.matches('[data-mode]')) {
            updateModeButtons();
        } else {
            button.disabled = wasDisabled;
        }
    }
}

function showDisconnected() {
    const mode = $('#mode');
    mode.className = modeClass('Disconnected');
    mode.textContent = modeLabel('Disconnected');
}

// --- Mode ------------------------------------------------------------------

async function loadSummary() {
    const alarm = await Api.get(`/panel/${encodeURIComponent(id)}`);

    const mode = $('#mode');
    mode.className = modeClass(alarm.mode);
    mode.textContent = modeLabel(alarm.mode);

    currentMode = alarm.mode;
    currentType = alarm.type;
    isPollingAlarm = !!alarm.isPollingAlarm;
    shouldAutoRefreshOpenDcs = !!alarm.shouldAutoRefreshOpenDcs;
    canViewMedia = !!alarm.canViewMedia;
    canControlDevices = !!alarm.canControlDevices;
    canViewHistory = !!alarm.canViewHistory;
    updateModeButtons();
    updatePairingGate();
    updateWalkTestGate();
    updateHistoryGate();
    updateControlGate();
    updateMediaGate();
}

// The open-devices list can be slow (some panel types do a live query for it), so it's loaded
// independently here rather than awaited - a slow panel must not hold up the mode buttons. HTTP
// polling alarms don't auto-load it at all; the user must hit Refresh to trigger that live query.
async function loadModeView() {
    await loadSummary().catch(reportError);

    if (isPollingAlarm) {
        $('#openDevices').innerHTML = '';
        const status = $('#noOpen');
        status.textContent = 'Hit refresh to display list';
        status.hidden = false;
    } else {
        loadOpen().catch(reportError);
    }
}

// A mode's own button is always redundant to press (the alarm is already there), and the two
// "in progress" modes aren't ones you can request directly, so nothing is enabled for them.
// They're treated as their target mode though, since e.g. "Home Arm Setting" already means the
// Home Arm button is redundant too.
function updateModeButtons() {
    const effectiveMode = currentMode === 'AwayArmInProgress' ? 'AwayArm'
        : currentMode === 'HomeArmInProgress' ? 'HomeArm'
        : currentMode;

    document.querySelectorAll('[data-mode]').forEach(btn => {
        btn.disabled = isModeChanging || btn.dataset.mode === effectiveMode;
    });
}

// The panel rejects entering learn/pairing mode unless it is disarmed, so reflect that here
// rather than let the request round-trip just to fail.
function updatePairingGate() {
    const allowed = currentMode === 'Disarm';

    const navLink = document.querySelector('nav a[data-view="pairing"]');
    if (navLink)
        navLink.classList.toggle('disabled', !allowed);

    const toggle = $('#learnToggle');
    if (toggle && learnState === 'idle')
        toggle.disabled = !allowed;
}

// Walk test puts the panel into a special mode the same way pairing/learn does, so it's gated
// the same way: only available while disarmed.
function updateWalkTestGate() {
    const navLink = document.querySelector('nav a[data-view="walktest"]');
    if (navLink)
        navLink.classList.toggle('disabled', currentMode !== 'Disarm');
}

// Hidden entirely rather than just disabled: the backend already knows definitively whether
// this alarm's protocol supports it.
function updateHistoryGate() {
    const navLink = document.querySelector('nav a[data-view="history"]');
    if (navLink)
        navLink.hidden = !canViewHistory;
}

// Hidden entirely rather than just disabled: the backend already knows definitively whether this
// alarm has a controllable device paired.
function updateControlGate() {
    const navLink = document.querySelector('nav a[data-view="control"]');
    if (navLink)
        navLink.hidden = !canControlDevices;
}

// Only shown for panels with a MediaSavePath configured on the backend.
function updateMediaGate() {
    const navLink = document.querySelector('nav a[data-view="media"]');
    if (navLink)
        navLink.hidden = !canViewMedia;
}

document.querySelectorAll('[data-mode]').forEach(btn => {
    btn.onclick = () => guard(btn, async () => {
        isModeChanging = true;
        updateModeButtons();
        try {
            const response = await Api.post(`/panel/${encodeURIComponent(id)}/mode`, {
                mode: btn.dataset.mode,
                user: Number($('#asUser').value || 0)
            });

            // Not a failure - the panel still applied the change - but worth calling out.
            if (response?.doorOpen)
                toast(`Set ${modeLabel(btn.dataset.mode)} - WARNING: Door/window reported open`, 'warn');
            else
                toast(`Set ${modeLabel(btn.dataset.mode)}`);

            await loadSummary();
        } finally {
            isModeChanging = false;
            updateModeButtons();
        }
    });
});

// --- Control ---------------------------------------------------------------
// Panes are sorted with power switches first, then cameras, each by index number.

async function loadControl() {
    const status = $('#noControlDevices');
    status.hidden = false;
    status.textContent = 'Loading\u2026';
    $('#controlPanes').innerHTML = '';

    const data = await Api.get(`/panel/${encodeURIComponent(id)}/devices`);
    const allDevices = data.devices ?? [];
    const states = data.states ?? [];

    const devices = allDevices
        .filter(d => d.type === 'PowerSwitch' || d.type === 'PirCamera')
        .sort((a, b) => a.type === b.type ? a.index - b.index : (a.type === 'PowerSwitch' ? -1 : 1));

    $('#controlPanes').innerHTML = devices.map(d => {
        // PowerOn is live state, not part of the device itself.
        const powerOn = states.find(s => s.device?.index === d.index)?.powerOn;

        return `
        <div class="pane body">
            <h3 class="subhead">${escapeHtml(d.name)} (index ${d.index})</h3>
            ${d.type === 'PowerSwitch' ? `
            <label class="switch">
                <input type="checkbox" class="power-toggle" data-index="${d.index}" ${powerOn ? 'checked' : ''} />
                <span class="slider"></span>
            </label>` : `
            <div class="row">
                <button class="primary request-media" data-index="${d.index}" data-flash="true">Request media</button>
                <button class="primary request-media" data-index="${d.index}" data-flash="false">Request media (no flash)</button>
            </div>`}
        </div>`;
    }).join('');

    status.textContent = 'No power switches or cameras to control.';
    status.hidden = devices.length > 0;

    $('#controlPanes').querySelectorAll('.power-toggle').forEach(toggle => {
        toggle.onchange = () => guard(toggle, async () => {
            const on = toggle.checked;
            try {
                await Api.post(`/panel/${encodeURIComponent(id)}/devices/${toggle.dataset.index}/power`, { on });
            } catch (e) {
                toggle.checked = !on;
                throw e;
            }
        });
    });

    $('#controlPanes').querySelectorAll('.request-media').forEach(btn => {
        btn.onclick = (e) => guard(e.target, async () => {
            await Api.post(`/panel/${encodeURIComponent(id)}/media/${btn.dataset.index}`, { flash: btn.dataset.flash === 'true' });
            toast('Media requested');
        });
    });
}

// --- Saved media -----------------------------------------------------------

async function loadMedia() {
    const status = $('#noMedia');
    status.hidden = false;
    status.textContent = 'Loading\u2026';
    $('#mediaGrid').innerHTML = '';

    const files = await Api.get(`/panel/${encodeURIComponent(id)}/media`);

    $('#mediaGrid').innerHTML = files.map(f => `
        <figure data-filename="${escapeHtml(f.fileName)}">
            <label class="check">
                <input type="checkbox" class="media-select" />
                <span class="muted">Select</span>
            </label>
            <img src="/panel/${encodeURIComponent(id)}/media/${encodeURIComponent(f.fileName)}" alt="${escapeHtml(f.fileName)}" loading="lazy" />
            <figcaption class="muted">${escapeHtml(f.takenAt ? new Date(f.takenAt).toLocaleString() : 'Unknown')}</figcaption>
        </figure>`).join('');

    status.textContent = 'No saved media.';
    status.hidden = files.length > 0;
}

$('#refreshMedia').onclick = (e) => guard(e.target, loadMedia);

$('#deleteSelectedMedia').onclick = (e) => {
    const selected = [...$('#mediaGrid').querySelectorAll('figure')]
        .filter(fig => fig.querySelector('.media-select').checked)
        .map(fig => fig.dataset.filename);

    if (!selected.length) {
        toast('No files selected', 'err');
        return;
    }

    if (!confirm(`Delete ${selected.length} file(s)?`)) return;

    guard(e.target, async () => {
        for (const fileName of selected)
            await Api.del(`/panel/${encodeURIComponent(id)}/media/${encodeURIComponent(fileName)}`);

        toast('Deleted');
        await loadMedia();
    });
};

// --- Windows and doors open ----------------------------------------------

async function loadOpen(showLoading = true) {
    const status = $('#noOpen');

    if (showLoading) {
        status.hidden = false;
        status.textContent = 'Loading\u2026';
        $('#openDevices').innerHTML = '';
    }

    const open = await Api.get(`/panel/${encodeURIComponent(id)}/open`);

    $('#openDevices').innerHTML = open.map(d => `
        <tr data-index="${d.index}">
            <td>${d.index}</td>
            <td><strong>${escapeHtml(d.name)}</strong></td>
            <td class="muted">${escapeHtml(d.statusLastChanged ? new Date(d.statusLastChanged).toLocaleString() : 'Unknown')}</td>
            <td style="text-align:right"><button class="small">Mark closed</button></td>
        </tr>`).join('');

    status.textContent = 'Nothing is reporting as open.';
    status.hidden = open.length > 0;

    $('#openDevices').querySelectorAll('tr').forEach(row => {
        row.querySelector('button').onclick = (e) => guard(e.target, async () => {
            await Api.post(`/panel/${encodeURIComponent(id)}/devices/${row.dataset.index}/markclosed`);
            toast('Marked as closed');
            await loadOpen();
        });
    });
}

$('#refreshOpen').onclick = (e) => guard(e.target, loadOpen);

// --- Devices -------------------------------------------------------------

async function loadDevices() {
    const status = $('#noDevices');
    status.hidden = false;
    status.textContent = 'Loading\u2026';
    $('#devices').innerHTML = '';

    const data = await Api.get(`/panel/${encodeURIComponent(id)}/devices`);
    const devices = data.devices ?? [];

    $('#devices').innerHTML = devices.map(d => `
        <tr class="clickable" data-index="${d.index}">
            <td>${d.index}</td>
            <td><strong>${escapeHtml(d.name)}</strong></td>
            <td class="muted">${escapeHtml(d.type)}</td>
            <td class="muted nowrap" style="text-align:right">Edit &rsaquo;</td>
        </tr>`).join('');

    status.textContent = 'No devices.';
    status.hidden = devices.length > 0;

    $('#devices').querySelectorAll('tr').forEach(row => {
        row.onclick = () => location.hash = `#device/${row.dataset.index}`;
    });
}

// --- Device detail -------------------------------------------------------

let editingIndex = null;

// Only these types are armed against an index attribute; the rest have no meaningful mode.
const MODE_DEVICE_TYPES = ['DoorContact', 'PIR', 'PirCamera'];

// Friendly names for the subset of attributes worth offering, keyed by the panel's own value.
const DEVICE_MODES = {
    Perimeter: 'Burglar',
    Entry1: 'Entry',
    Interior: 'Home Omit',
    HomeDelay: 'Home Access'
};

function fillModes(current) {
    const modes = { ...DEVICE_MODES };

    // Keep whatever the device is actually set to selectable, even when it is outside the four
    // offered, so saving cannot silently change it.
    if (current && !modes[current])
        modes[current] = current;

    $('#deviceMode').innerHTML = Object.entries(modes)
        .map(([value, label]) =>
            `<option value="${escapeHtml(value)}"${value === current ? ' selected' : ''}>${escapeHtml(label)}</option>`)
        .join('');
}

async function loadDevice(index) {
    const data = await Api.get(`/panel/${encodeURIComponent(id)}/devices`);
    const device = (data.devices ?? []).find(d => String(d.index) === String(index));

    if (!device) {
        toast(`There is no device at index ${index}`, 'err');
        location.hash = '#devices';
        return;
    }

    editingIndex = device.index;

    $('#deviceIndex').textContent = device.index;
    $('#deviceType').textContent = device.type;
    $('#deviceRadioId').textContent = device.radioIdentifier || '-';
    $('#deviceName').value = device.name;
    $('#deviceName').maxLength = maxDeviceNameLength;

    $('#deviceModeField').hidden = !MODE_DEVICE_TYPES.includes(device.type);
    fillModes(device.attribute);

    $('#deviceBypass').checked = !!device.bypass;
}

$('#deviceBack').onclick = () => location.hash = '#devices';

$('#saveDevice').onclick = (e) => guard(e.target, async () => {
    const body = { name: $('#deviceName').value, bypass: $('#deviceBypass').checked };

    if (!$('#deviceModeField').hidden)
        body.attribute = $('#deviceMode').value;

    await Api.post(`/panel/${encodeURIComponent(id)}/devices/${editingIndex}`, body);
    toast('Device saved');
    location.hash = '#devices';
});

$('#deleteDevice').onclick = (e) => {
    if (!confirm(`Delete the device at index ${editingIndex}?`)) return;

    guard(e.target, async () => {
        await Api.del(`/panel/${encodeURIComponent(id)}/devices/${editingIndex}`);
        toast('Device deleted');

        // Deleting the last power switch/camera should hide Control again.
        await loadSummary();

        location.hash = '#devices';
    });
};

// --- Users ---------------------------------------------------------------

async function loadUsers() {
    const users = await Api.get(`/panel/${encodeURIComponent(id)}/users`);

    $('#users').innerHTML = users.map(u => `
        <tr data-index="${u.index}">
            <td data-label="Slot">${u.index}</td>
            <td data-label="Name"><input class="u-name" value="${escapeHtml(u.name)}" maxlength="10" /></td>
            <td data-label="PIN"><input class="u-pin" value="${escapeHtml(u.pin ?? '')}" inputmode="numeric"
                      placeholder="${u.pinMasked ? 'Hidden - retype to save' : ''}" /></td>
            <td data-label="CID"><input class="u-latch" type="checkbox" ${u.latch ? 'checked' : ''} /></td>
            <td class="actions" style="text-align:right">
                <button class="small u-save">Save</button>
                ${u.index === 1 ? '' : '<button class="small danger u-del">Delete</button>'}
            </td>
        </tr>`).join('');

    $('#asUser').innerHTML = users
        .map(u => `<option value="${u.index}">as ${escapeHtml(u.name)}</option>`)
        .join('');

    $('#users').querySelectorAll('tr').forEach(row => {
        const index = row.dataset.index;

        row.querySelector('.u-save').onclick = (e) => guard(e.target, async () => {
            await Api.post(`/panel/${encodeURIComponent(id)}/users/${index}`, {
                name: row.querySelector('.u-name').value,
                pin: row.querySelector('.u-pin').value,
                latch: row.querySelector('.u-latch').checked
            });
            toast('User saved');
            await loadUsers();
        });

        const del = row.querySelector('.u-del');
        if (del) del.onclick = (e) => {
            if (!confirm(`Delete user in slot ${index}?`)) return;
            guard(e.target, async () => {
                await Api.del(`/panel/${encodeURIComponent(id)}/users/${index}`);
                toast('User deleted');
                await loadUsers();
            });
        };
    });
}

$('#addUser').onclick = (e) => guard(e.target, async () => {
    const result = await Api.post(`/panel/${encodeURIComponent(id)}/users`, {
        name: $('#newName').value,
        pin: $('#newPin').value,
        latch: $('#newLatch').checked
    });
    toast(`Added user in slot ${result.index}`);
    $('#newName').value = '';
    $('#newPin').value = '';
    $('#newLatch').checked = false;
    await loadUsers();
});

// --- History -------------------------------------------------------------

// Panel timestamps are wall-clock readings from the alarm itself, with no timezone, so they are
// shown verbatim rather than being converted to the browser's locale.
function formatPanelTime(iso) {
    return iso.replace('T', ' ').replace(/\..*$/, '');
}

function describeEntry(entry) {
    let text = entry.description;

    if (entry.user) text += ` by ${entry.user}`;
    if (entry.device) text += ` ${entry.preposition} ${entry.device}`;

    return text;
}

async function loadHistory() {
    const status = $('#historyStatus');
    status.hidden = false;
    status.textContent = 'Loading\u2026';

    const entries = await Api.get(`/panel/${encodeURIComponent(id)}/history`);

    $('#entries').innerHTML = entries.map((e, i) => {
        const age = (new Date(e.alarmClockWhenQueried) - new Date(e.whenOccurred)) / 1000;

        return `
            <tr>
                <td class="muted" data-label="#">${i + 1}</td>
                <td data-label="Event">${escapeHtml(describeEntry(e))}</td>
                <td class="muted" data-label="Time">${escapeHtml(formatPanelTime(e.whenOccurred))}</td>
                <td class="muted" data-label="Age">${escapeHtml(formatAge(age))}</td>
            </tr>`;
    }).join('');

    if (!entries.length) {
        status.textContent = 'No history returned.';
        $('#clock').textContent = '';
        return;
    }

    status.hidden = true;

    // Surface panel clock drift, since it makes the timestamps above misleading.
    const panelClock = new Date(entries[0].alarmClockWhenQueried);
    const drift = Math.round((Date.now() - panelClock) / 1000);

    $('#clock').textContent = `Panel clock ${formatPanelTime(entries[0].alarmClockWhenQueried)}`
        + (Math.abs(drift) > 120
            ? ` (${formatAge(Math.abs(drift)).replace(' ago', '')} ${drift > 0 ? 'behind' : 'ahead of'} this machine)`
            : '');
}

$('#refreshHistory').onclick = (e) => guard(e.target, loadHistory);

// --- Pairing -------------------------------------------------------------
// Mirrors the walk test stream: opening it starts learn mode, and the initial ack (sent only once
// the panel has actually confirmed entering it) is what lets the client show a genuine "entering
// learn mode" wait rather than assuming success immediately. Closing it after an explicit
// /learn/stop call is what takes the panel back out again.

const LEARN_TIMEOUT = 300;
const LEARN_IDLE_MESSAGE = 'Puts the panel into learn mode, then waits for a device to transmit.';

let learnSource = null;
let learnState = 'idle'; // 'idle' | 'connecting' | 'searching' | 'found' | 'stopping'
let learnCountdown = null;

function showLearn(state) {
    learnState = state;

    const toggle = $('#learnToggle');
    const busy = state === 'connecting' || state === 'stopping';

    $('#learnControls').hidden = state === 'found';
    $('#learnFound').hidden = state !== 'found';

    const existingSpinner = toggle.querySelector('.spinner');
    if (existingSpinner) existingSpinner.remove();

    toggle.textContent = state === 'idle' ? 'Start pairing'
        : state === 'connecting' ? 'Entering learn mode'
        : state === 'stopping' ? 'Pairing stopping'
        : 'Stop pairing';

    toggle.classList.toggle('primary', state === 'idle');
    toggle.classList.toggle('danger', state !== 'idle');
    toggle.classList.toggle('busy', busy);

    // Disabled only while stopping, to avoid a second concurrent stop - still clickable while
    // connecting or searching so the user can cancel either.
    toggle.disabled = state === 'stopping';

    if (busy) {
        const spinner = document.createElement('span');
        spinner.className = 'spinner';
        toggle.insertBefore(spinner, toggle.firstChild);
    }

    if (state === 'idle' || state === 'connecting' || state === 'stopping')
        $('#learnMessage').textContent = state === 'idle'
            ? LEARN_IDLE_MESSAGE
            : 'Waiting for the panel to acknowledge - this can take a while over a slow connection.';
}

function closeLearnStream() {
    learnSource?.close();
    learnSource = null;
}

function startLearnCountdown() {
    let remaining = LEARN_TIMEOUT;
    const tick = () => $('#learnMessage').textContent =
        `Listening - trigger the device now. ${Math.max(remaining, 0)}s remaining.`;

    tick();
    learnCountdown = setInterval(() => {
        remaining--;
        tick();

        if (remaining <= 0) {
            stopLearnCountdown();
            stopPairing();
            toast('No device detected within the timeout', 'err');
        }
    }, 1000);
}

function stopLearnCountdown() {
    clearInterval(learnCountdown);
    learnCountdown = null;
}

function startPairing() {
    showLearn('connecting');

    learnSource = new EventSource(`/panel/${encodeURIComponent(id)}/learn/stream`);
    let acknowledged = false;

    // The server doesn't send anything until the panel has acknowledged entering learn mode
    // (which can take a while over the polling transport), so "open" is the actual confirmation,
    // and only from here do we actually know the listening window has started.
    learnSource.onopen = () => {
        acknowledged = true;
        showLearn('searching');
        startLearnCountdown();
    };

    learnSource.onmessage = (event) => {
        stopLearnCountdown();

        const device = JSON.parse(event.data);
        $('#foundId').textContent = device.radioIdentifier;
        $('#foundType').textContent = device.type;
        $('#foundName').value = '';
        $('#foundName').maxLength = maxDeviceNameLength;
        showLearn('found');
        $('#foundName').focus();
    };

    // EventSource reconnects by itself, and every reconnect would restart learn mode on the
    // panel, so any error is treated as terminal. The connection is already gone, so there's
    // nothing to wait for - just clean up locally.
    learnSource.onerror = () => {
        stopLearnCountdown();
        closeLearnStream();
        showLearn('idle');
        updatePairingGate();
        toast(acknowledged
            ? 'Pairing stopped: the connection to the alarm was lost.'
            : 'Failed to enter learn mode.', 'err');
    };
}

// Stops learn mode and waits for the panel to actually acknowledge that before tearing down the
// stream, so the button can show a spinner for the real duration rather than assuming success
// the instant the stream is closed.
async function stopPairing() {
    if (!learnSource || learnState === 'stopping')
        return;

    stopLearnCountdown();
    showLearn('stopping');

    try {
        await Api.post(`/panel/${encodeURIComponent(id)}/learn/stop`);
    } catch (e) {
        reportError(e);
    }

    closeLearnStream();
    showLearn('idle');
    updatePairingGate();
}

$('#learnToggle').onclick = async () => {
    if (learnState === 'idle') {
        // currentMode isn't kept fresh while viewing this tab (mode polling only runs on the
        // Mode view), so re-check against the panel's actual current mode before gating.
        await loadSummary().catch(reportError);

        if (currentMode !== 'Disarm') {
            toast('The alarm must be disarmed before pairing or learning can start.', 'err');
            return;
        }

        startPairing();
    } else if (learnState === 'connecting' || learnState === 'searching') {
        stopPairing();
    }
    // Ignore clicks while stopping - already in flight. Not reachable while found (hidden then).
};

$('#addLearned').onclick = (e) => guard(e.target, async () => {
    const name = $('#foundName').value.trim();

    if (!name) {
        toast('Enter a name for the device', 'err');
        return;
    }

    await Api.post(`/panel/${encodeURIComponent(id)}/learn/add`, { name });
    closeLearnStream();
    toast('Device added');
    showLearn('idle');

    // Refreshes the capability flags too, so Control shows up immediately if this was the
    // first power switch/camera paired.
    await loadSummary();

    // Switching view reloads the device list, so no separate refresh is needed for that.
    location.hash = '#devices';
});

$('#discardLearned').onclick = (e) => guard(e.target, async () => {
    await Api.post(`/panel/${encodeURIComponent(id)}/learn/stop`);
    closeLearnStream();
    showLearn('idle');
    updatePairingGate();
});

window.addEventListener('beforeunload', () => {
    // beforeunload can't wait for either acknowledgement - best effort only.
    if (learnSource) {
        fetch(`/panel/${encodeURIComponent(id)}/learn/stop`, { method: 'POST', keepalive: true });
        closeLearnStream();
    }

    if (walkSource) {
        fetch(`/panel/${encodeURIComponent(id)}/walktest/stop`, { method: 'POST', keepalive: true });
        closeWalkStream();
    }
});

// --- Walk test -----------------------------------------------------------
// Opening the stream starts the walk test and closing it stops it, so the connection itself is
// the on/off switch - there is no separate stop call.

const WALK_MAX_ROWS = 200;
let walkSource = null;
let walkState = 'idle'; // 'idle' | 'connecting' | 'running' | 'stopping'

function showWalk(state) {
    walkState = state;

    const toggle = $('#walkToggle');
    const busy = state === 'connecting' || state === 'stopping';

    const existingSpinner = toggle.querySelector('.spinner');
    if (existingSpinner) existingSpinner.remove();

    toggle.textContent = state === 'idle' ? 'Start walk test'
        : state === 'connecting' ? 'Walk test starting'
        : state === 'stopping' ? 'Walk test stopping'
        : 'Stop walk test';

    toggle.classList.toggle('primary', state === 'idle');
    toggle.classList.toggle('danger', state !== 'idle');
    toggle.classList.toggle('busy', busy);

    // Still clickable while connecting - closing the stream cancels a pending connect just as
    // well as it stops a running test. Disabled while stopping to avoid a second concurrent stop.
    toggle.disabled = state === 'stopping';

    if (busy) {
        const spinner = document.createElement('span');
        spinner.className = 'spinner';
        toggle.insertBefore(spinner, toggle.firstChild);
    }

    $('#walkMessage').textContent = state === 'connecting' || state === 'stopping'
        ? 'Waiting for the panel to acknowledge - this can take a while over a slow connection.'
        : state === 'running'
            ? 'Running - trigger each device in turn.'
            : 'Puts the panel into walk test, then reports each device as it is triggered.';
}

// The API normalises readings to 1-10; the meter shows five bars, so each covers two steps.
function signalBars(signal) {
    const level = Math.ceil(signal.strength / 2);
    const tone = level <= 2 ? 'weak' : level <= 3 ? 'fair' : 'good';

    const bars = [1, 2, 3, 4, 5]
        .map(n => `<i class="${n <= level ? 'on' : ''}"></i>`)
        .join('');

    const title = `Strength ${signal.strength}/10 (panel reported ${signal.rssi} of ${signal.maxRssi})`;

    return `<span class="bars ${tone}" title="${escapeHtml(title)}">${bars}</span>`;
}

function addWalkSignal(signal) {
    const row = document.createElement('tr');

    // The panel does not timestamp these, so record when we saw it.
    row.innerHTML = `
        <td class="muted">${new Date().toLocaleTimeString()}</td>
        <td>${signal.deviceIndex ?? '-'}</td>
        <td><strong>${escapeHtml(signal.deviceName ?? 'Unrecognised device')}</strong></td>
        <td>${signalBars(signal)}</td>`;

    const tbody = $('#walkSignals');
    tbody.prepend(row);

    while (tbody.children.length > WALK_MAX_ROWS)
        tbody.lastElementChild.remove();

    $('#noWalkSignals').hidden = true;
}

function startWalkTest() {
    showWalk('connecting');

    walkSource = new EventSource(`/panel/${encodeURIComponent(id)}/walktest/stream`);
    let started = false;

    // The server doesn't send anything until the panel has acknowledged entering walk test mode
    // (which can take a while over the polling transport), so "open" is the actual confirmation -
    // not just "the button was clicked".
    walkSource.onopen = () => {
        started = true;
        showWalk('running');
    };

    walkSource.onmessage = (event) => addWalkSignal(JSON.parse(event.data));

    // EventSource reconnects by itself, and every reconnect would restart the walk test on the
    // panel, so any error is treated as terminal. The connection is already gone at this point,
    // so there's nothing to wait for - just clean up locally.
    walkSource.onerror = () => {
        closeWalkStream();
        showWalk('idle');
        toast(started
            ? 'Walk test stopped: the connection to the alarm was lost.'
            : 'Failed to start walk test.', 'err');
    };
}

function closeWalkStream() {
    walkSource?.close();
    walkSource = null;
}

// Stops the panel's walk test and waits for it to actually acknowledge that before tearing down
// the stream, so the button can show a spinner for the real duration rather than assuming success
// the instant the stream is closed.
async function stopWalkTest() {
    if (!walkSource || walkState === 'stopping')
        return;

    showWalk('stopping');

    try {
        await Api.post(`/panel/${encodeURIComponent(id)}/walktest/stop`);
    } catch (e) {
        reportError(e);
    }

    closeWalkStream();
    showWalk('idle');
}

$('#walkToggle').onclick = () => {
    if (walkState === 'idle')
        startWalkTest();
    else if (walkState === 'running')
        stopWalkTest();
    // Ignore clicks while connecting or stopping - already in flight.
};

// --- Info ------------------------------------------------------------------

async function loadInfo() {
    const alarm = await Api.get(`/panel/${encodeURIComponent(id)}`);

    $('#identifier').textContent = alarm.identifier;
    $('#type').textContent = alarm.type;
    $('#version').textContent = alarm.version ?? '-';
    $('#lastUser').textContent = alarm.lastModeChangeUser || '-';

    const ipAddress = $('#ipAddress');

    // The panel has its own web admin on port 80.
    ipAddress.innerHTML = alarm.ipAddress
        ? `<a href="http://${escapeHtml(alarm.ipAddress)}/" target="_blank" rel="noopener">${escapeHtml(alarm.ipAddress)}</a>`
        : '-';
}

// --- System config --------------------------------------------------------
// Away/Home Arm delays and sounds, general configuration and siren settings. Each pane saves
// independently - the server takes care of preserving whatever fields a save doesn't own but
// which share the same underlying alarm command.

const DELAY_OPTIONS = Array.from({ length: 13 }, (_, i) => [i * 10, i === 0 ? 'No delay' : `${i * 10} seconds`]);

const SOUND_OPTIONS = [[0, 'No sound'], [1, 'Low'], [2, 'Medium'], [3, 'High']];

const SUPERVISION_OPTIONS = [[0, 'Disable'], [1440, '24 hours']];

const SIREN_LENGTH_OPTIONS = Array.from({ length: 10 }, (_, i) => [i + 1, `${i + 1} minute${i ? 's' : ''}`]);

const SIREN_TOGGLE_OPTIONS = [[0, 'Disabled'], [1, 'Enabled']];

const COMFORT_LED_OPTIONS = [
    [0, 'Disabled'], [1, '5 seconds'], [2, '10 seconds'], [3, '20 seconds'],
    [4, '30 seconds'], [5, '40 seconds'], [6, '50 seconds'], [7, '60 seconds']
];

function fillConfigOptions(select, options, current) {
    select.innerHTML = options
        .map(([value, label]) => `<option value="${value}"${value === current ? ' selected' : ''}>${escapeHtml(label)}</option>`)
        .join('');
}

// Siren settings can never be read back, so every load starts them on "Please select" rather
// than implying a value that may not be what the panel actually has.
function fillSirenOptions(select, options) {
    select.innerHTML = '<option value="">Please select</option>'
        + options.map(([value, label]) => `<option value="${value}">${escapeHtml(label)}</option>`).join('');
}

async function loadSystemConfig() {
    $('#pollingConfigWarning').hidden = !isPollingAlarm;

    const config = await Api.get(`/panel/${encodeURIComponent(id)}/config`);

    fillSirenOptions($('#sirenComfortLed'), COMFORT_LED_OPTIONS);
    fillSirenOptions($('#sirenTamper'), SIREN_TOGGLE_OPTIONS);
    fillSirenOptions($('#sirenEntryExitConfirm'), SIREN_TOGGLE_OPTIONS);

    fillConfigOptions($('#awayEntryDelay'), DELAY_OPTIONS, config.awayArmEntryDelay);
    fillConfigOptions($('#awayExitDelay'), DELAY_OPTIONS, config.awayArmExitDelay);
    fillConfigOptions($('#awayEntryDelaySound'), SOUND_OPTIONS, config.awayArmEntryDelaySound);
    fillConfigOptions($('#awayExitDelaySound'), SOUND_OPTIONS, config.awayArmExitDelaySound);

    fillConfigOptions($('#homeEntryDelay'), DELAY_OPTIONS, config.homeArmEntryDelay);
    fillConfigOptions($('#homeExitDelay'), DELAY_OPTIONS, config.homeArmExitDelay);
    fillConfigOptions($('#homeEntryDelaySound'), SOUND_OPTIONS, config.homeArmEntryDelaySound);
    fillConfigOptions($('#homeExitDelaySound'), SOUND_OPTIONS, config.homeArmExitDelaySound);

    fillConfigOptions($('#doorContactSound'), SOUND_OPTIONS, config.doorContactSound);
    fillConfigOptions($('#supervision'), SUPERVISION_OPTIONS, config.supervision);
    fillConfigOptions($('#sirenLength'), SIREN_LENGTH_OPTIONS, config.sirenLength);
}

$('#saveAwayArmConfig').onclick = (e) => guard(e.target, async () => {
    await Api.post(`/panel/${encodeURIComponent(id)}/config/awayarm`, {
        entryDelay: Number($('#awayEntryDelay').value),
        exitDelay: Number($('#awayExitDelay').value),
        entryDelaySound: Number($('#awayEntryDelaySound').value),
        exitDelaySound: Number($('#awayExitDelaySound').value)
    });
    toast('Away Arm settings saved');
});

$('#saveHomeArmConfig').onclick = (e) => guard(e.target, async () => {
    await Api.post(`/panel/${encodeURIComponent(id)}/config/homearm`, {
        entryDelay: Number($('#homeEntryDelay').value),
        exitDelay: Number($('#homeExitDelay').value),
        entryDelaySound: Number($('#homeEntryDelaySound').value),
        exitDelaySound: Number($('#homeExitDelaySound').value)
    });
    toast('Home Arm settings saved');
});

$('#saveGeneralConfig').onclick = (e) => guard(e.target, async () => {
    await Api.post(`/panel/${encodeURIComponent(id)}/config/general`, {
        doorContactSound: Number($('#doorContactSound').value),
        supervision: Number($('#supervision').value),
        sirenLength: Number($('#sirenLength').value)
    });
    toast('General configuration saved');
});

// Each siren setting is saved on its own, since the panel can only ever be told to program one
// at a time and none can be verified by reading it back afterwards.
function saveSirenSetting(button, select, setting, label) {
    return guard(button, async () => {
        if (select.value === '') {
            toast(`Select a value for ${label} first`, 'err');
            return;
        }

        await Api.post(`/panel/${encodeURIComponent(id)}/config/siren`, {
            setting,
            value: Number(select.value)
        });
        toast(`${label} saved`);
    });
}

$('#saveSirenComfortLed').onclick = (e) => saveSirenSetting(e.target, $('#sirenComfortLed'), 'ComfortLed', 'Comfort LED');
$('#saveSirenTamper').onclick = (e) => saveSirenSetting(e.target, $('#sirenTamper'), 'TamperDetection', 'Tamper detection');
$('#saveSirenEntryExitConfirm').onclick = (e) => saveSirenSetting(e.target, $('#sirenEntryExitConfirm'), 'EntryExitConfirm', 'Entry/exit confirm');

// --- Navigation ----------------------------------------------------------
// Views load on activation rather than up front, so switching away from a slow one (history
// issues live panel commands) doesn't hold up the rest of the page.

const Views = {
    mode: loadModeView,
    control: loadControl,
    media: loadMedia,
    history: loadHistory,
    users: loadUsers,
    walktest: null,
    devices: loadDevices,
    pairing: null,
    device: loadDevice,
    config: loadSystemConfig,
    info: loadInfo
};

let modePoll = null;

function activate(route) {
    // Routes are "name" or "name/param", e.g. "device/2".
    const [requested, param] = (route || '').split('/');
    const name = Object.hasOwn(Views, requested) ? requested : 'mode';

    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));

    // The device editor is reached from Devices, so keep that nav entry highlighted.
    const navFor = name === 'device' ? 'devices' : name;
    document.querySelectorAll('nav a').forEach(a => a.classList.toggle('active', a.dataset.view === navFor));

    clearInterval(modePoll);
    modePoll = null;

    // Leaving the pairing view would otherwise strand the panel in learn mode.
    if (name !== 'pairing' && learnSource)
        stopPairing();

    // Likewise, the panel stays in walk test for as long as the stream is open.
    if (name !== 'walktest')
        stopWalkTest();

    const load = Views[name];
    if (load) load(param).catch(reportError);

    // The panel reports mode changes over its own connection, so poll to pick those up.
    if (name === 'mode') {
        modePoll = setInterval(async () => {
            try {
                await loadSummary();

                // Server-computed: both CTC types already get a live read of this on every view
                // load/Refresh, so only ML benefits from polling it here too.
                if (shouldAutoRefreshOpenDcs)
                    await loadOpen(false);
            } catch (e) {
                // Anything else is likely transient, but a gone alarm will not come back on its
                // own - stop polling and say so once rather than every five seconds.
                if (e.status === 404) {
                    clearInterval(modePoll);
                    modePoll = null;
                    showDisconnected();
                    reportError(e);
                }
            }
        }, 5000);
    }
}

window.addEventListener('hashchange', () => activate(location.hash.slice(1)));

// A disabled nav entry still has a real href (so it works once re-enabled), so the click itself
// has to be the thing that's blocked.
document.querySelectorAll('nav a[data-view="pairing"]').forEach(link => {
    link.addEventListener('click', (e) => {
        if (link.classList.contains('disabled')) {
            e.preventDefault();
            toast('The alarm must be disarmed before pairing or learning can start.', 'err');
        }
    });
});

document.querySelectorAll('nav a[data-view="walktest"]').forEach(link => {
    link.addEventListener('click', (e) => {
        if (link.classList.contains('disabled')) {
            e.preventDefault();
            toast('The alarm must be disarmed before walk test can start.', 'err');
        }
    });
});

async function init() {
    try {
        const alarms = await Api.get('/panel/list');
        const switcher = $('#switcher');

        switcher.innerHTML = alarms
            .map(a => `<option value="${escapeHtml(a.identifier)}">${escapeHtml(a.friendlyName || a.identifier)}</option>`)
            .join('');

        // No id in the URL (or one that has since disconnected): fall back to the first alarm.
        if (!id || !alarms.some(a => a.identifier === id))
            id = alarms.length ? alarms[0].identifier : null;

        switcher.value = id ?? '';
        switcher.onchange = () =>
            location.href = `/alarm.html?id=${encodeURIComponent(switcher.value)}${location.hash}`;

        // The name limit is the panel's, not ours - 26 on ML, 10 on CTC.
        const chosen = alarms.find(a => a.identifier === id);

        $('#brandId').textContent = (chosen?.friendlyName || id) ?? 'none connected';

        // Set from the same summary loadSummary uses, so the nav is right before the first
        // activation and doesn't need a dedicated fetch just for this.
        if (chosen) {
            maxDeviceNameLength = chosen.maxDeviceNameLength;
            isPollingAlarm = !!chosen.isPollingAlarm;
            shouldAutoRefreshOpenDcs = !!chosen.shouldAutoRefreshOpenDcs;
            canViewMedia = !!chosen.canViewMedia;
            canControlDevices = !!chosen.canControlDevices;
            canViewHistory = !!chosen.canViewHistory;
            updateControlGate();
            updateHistoryGate();
            updateMediaGate();
        }

        $('#foundName').maxLength = maxDeviceNameLength;

        if (!id) {
            toast('No alarms are connected', 'err');
            return;
        }

        // Users are loaded up front regardless of view, since Status needs them for the
        // "as user" picker that arm/disarm sends.
        await loadUsers();

        activate(location.hash.slice(1));
    } catch (e) {
        reportError(e);
    }
}

init();
