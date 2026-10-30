// The Earthstorm devices' pages, as PlasmaStick's: the API, tabs, the header and footer, the settings forms (fields
// named "section.key", filled from GET /api/config and saved to POST /api/config with only what changed), and for a
// light its controls (Base.light) and schedule (Base.schedule).
// Values are set with .value / .textContent, never as HTML, so names can't inject markup. Save stays disabled until
// the settings have loaded, so a form can't post empty fields over the real ones.
var Base = (function () {
  var el = function (id) { return document.getElementById(id); };
  var info = null, config = null, restarting = false, infoListeners = [];

  function api(path, body) {
    var options = { cache: 'no-store' };
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(body);
    }
    return fetch(path, options).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
        return data;
      });
    });
  }

  function duration(s) {
    if (s == null) return 'unknown';
    var d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return d ? d + 'd ' + h + 'h' : h ? h + 'h ' + m + 'm' : m + 'm';
  }

  function say(id, text, good) {
    var m = el(id);
    if (!m) return;
    m.textContent = text || '';
    m.className = good ? 'msg good' : 'msg';
  }

  function showAlert(text) {
    el('alert').textContent = text;
    el('alert').hidden = !text;
  }

  function mk(tag, props, kids) {
    var e = document.createElement(tag);
    for (var k in props) {
      if (k === 'text') e.textContent = props[k];
      else if (k.slice(0, 5) === 'aria-' || k.slice(0, 5) === 'data-') e.setAttribute(k, props[k]);
      else e[k] = props[k];
    }
    (kids || []).forEach(function (c) { e.appendChild(c); });
    return e;
  }

  // Tabs: the radios (name="t") and their labels; the chosen one is remembered for the next visit
  function tabs() {
    var radios = document.querySelectorAll('input[name=t]');
    function show() {
      radios.forEach(function (r) {
        var label = document.querySelector('nav label[for=' + r.id + ']');
        var pane = el(r.dataset.pane);
        label.classList.toggle('on', r.checked);
        label.classList.toggle('focus', r.checked && r === document.activeElement && r.matches(':focus-visible'));
        if (pane) pane.classList.toggle('on', r.checked);
      });
    }
    radios.forEach(function (r) {
      r.addEventListener('change', function () {
        try { localStorage.setItem('tab', r.id); } catch (e) {}
        show();
      });
      r.addEventListener('focus', show);
      r.addEventListener('blur', show);
    });
    try {
      var saved = localStorage.getItem('tab');
      if (saved && el(saved)) el(saved).checked = true;
    } catch (e) {}
    // A link can open a tab: #ts for the Schedule tab, say
    var linked = location.hash && el(location.hash.slice(1));
    if (linked && linked.name === 't') linked.checked = true;
    show();
  }

  // The settings forms. A form's extra(updates) can add what isn't a field (the schedule's events).

  var forms = function () { return document.querySelectorAll('form.settings'); };
  var fields = function (f) {
    return Array.prototype.filter.call(f.querySelectorAll('[name]'), function (e) { return !e.disabled; });
  };

  function fieldValue(f) {
    if (f.type === 'checkbox') return f.checked;
    if (f.type === 'number' || f.dataset.number !== undefined) return f.value === '' ? null : Number(f.value);
    return f.value;
  }
  // As text, to tell whether it changed (a tick box's value never changes, so whether it's ticked)
  function shownValue(f) { return f.type === 'checkbox' ? String(f.checked) : f.value; }

  function fill(c) {
    config = c;
    forms().forEach(function (form) {
      fields(form).forEach(function (f) {
        var parts = f.name.split('.'), value = (c[parts[0]] || {})[parts[1]];
        if (f.type === 'password') { f.value = ''; return; }
        if (value === undefined) return;
        if (f.type === 'checkbox') f.checked = !!value; else f.value = value === null ? '' : value;
      });
      remember(form);
      var save = form.querySelector('.save');
      if (save) save.disabled = false;
    });
    document.querySelectorAll('[data-password-set]').forEach(function (n) {
      var parts = n.dataset.passwordSet.split('.');
      n.hidden = !(c[parts[0]] || {})[parts[1] + '_set'];
    });
  }

  function remember(form) {
    fields(form).forEach(function (f) { f.dataset.saved = f.type === 'password' ? '' : shownValue(f); });
    showSave(form);
  }

  function changedFields(form) {
    return fields(form).filter(function (f) { return shownValue(f) !== f.dataset.saved; });
  }

  // The Save button says beforehand when saving restarts the device
  function showSave(form) {
    var save = form.querySelector('.save'), restartKeys = (config && config._restart) || [];
    if (!save) return;
    var restart = changedFields(form).some(function (f) { return restartKeys.indexOf(f.name) >= 0; });
    save.textContent = restart ? 'Save & Restart' : 'Save';
  }

  function loadSettings() {
    return api('/api/config').then(fill).catch(function (e) {
      showAlert('Could not load the settings (' + e.message + '). Reload to try again.');
    });
  }

  function settingsForms() {
    forms().forEach(function (form) {
      form.addEventListener('input', function () { showSave(form); });
      form.addEventListener('change', function () { showSave(form); });
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var msg = form.querySelector('.msg'), save = form.querySelector('.save'), updates = {};
        fields(form).forEach(function (f) { f.removeAttribute('aria-invalid'); });
        changedFields(form).forEach(function (f) {
          if (f.type === 'password' && !f.value) return;
          var parts = f.name.split('.');
          (updates[parts[0]] = updates[parts[0]] || {})[parts[1]] = fieldValue(f);
        });
        if (form.extra) form.extra(updates);
        if (!Object.keys(updates).length) { msg.textContent = 'Nothing has changed.'; msg.className = 'msg'; return; }
        msg.textContent = 'Saving…';
        msg.className = 'msg good';
        save.disabled = true;
        api('/api/config', updates).then(function (r) {
          if (r.restart_required) {
            msg.textContent = 'Saved. Restarting…';
            waitForRestart(msg);
            return;
          }
          msg.textContent = 'Saved.';
          save.disabled = false;
          loadSettings();
        }).catch(function (e) {
          msg.className = 'msg';
          msg.textContent = 'Not saved: ' + e.message;
          save.disabled = false;
          // Errors start with the setting's name (mqtt.topic_base ...): point at that field
          var name = (/^[\w]+\.[\w]+/.exec(e.message) || [''])[0];
          var bad = form.querySelector('[name="' + name + '"]');
          if (bad) {
            var d = bad.closest('details');
            if (d) d.open = true;
            bad.setAttribute('aria-invalid', 'true');
            bad.focus();
          }
        });
      });
    });
    document.querySelectorAll('[data-clear-password]').forEach(function (b) {
      b.addEventListener('click', function () {
        var parts = b.dataset.clearPassword.split('.'), body = {};
        body[parts[0]] = {};
        body[parts[0]][parts[1]] = null;
        api('/api/config', body).then(loadSettings);
      });
    });
  }

  // Waits until the device answers again, freshly started, then reloads
  function waitForRestart(msg) {
    var start = Date.now();
    restarting = true;
    function poll() {
      if ((Date.now() - start) / 1000 > 60 && msg) {
        msg.textContent = 'Still restarting. This is taking longer than usual; reload the page once it answers.';
      }
      api('/api/info').then(function (i) {
        var up = i.app_uptime_s != null ? i.app_uptime_s : i.uptime_s;
        if (up <= (Date.now() - start) / 1000 + 1) location.reload(); else setTimeout(poll, 1500);
      }).catch(function () { setTimeout(poll, 1500); });
    }
    setTimeout(poll, 2000);
  }

  // Restart and reset buttons (data-restart, data-reset with data-confirm)
  function actions() {
    document.querySelectorAll('[data-restart]').forEach(function (b) {
      b.addEventListener('click', function () {
        var msg = el(b.dataset.msg);
        api('/api/restart', {}).then(function () { say(b.dataset.msg, 'Restarting…', true); waitForRestart(msg); });
      });
    });
    // Restarting the Pi (a Juggle app): shown only where the OS takes the app's requests
    document.querySelectorAll('[data-reboot]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (!confirm(b.dataset.confirm)) return;
        var msg = el(b.dataset.msg);
        api('/api/reboot', { confirm: true }).then(function () {
          say(b.dataset.msg, 'Restarting the Pi… (about a minute)', true);
          waitForRestart(msg);
        }).catch(function (e) { say(b.dataset.msg, 'Not restarted: ' + e.message); });
      });
    });
    // The OS's setup mode (renaming the Pi, its Wi-Fi) and factory reset: the Pi restarts into setup mode, so the page
    // doesn't wait for it to come back, but says where to go
    [['data-setup-mode', '/api/setup-mode'], ['data-factory-reset', '/api/factory-reset']].forEach(function (a) {
      document.querySelectorAll('[' + a[0] + ']').forEach(function (b) {
        b.addEventListener('click', function () {
          if (!confirm(b.dataset.confirm)) return;
          api(a[1], { confirm: true }).then(function () {
            restarting = true;
            // A Pico gives its own hotspot's name (and has no Ethernet)
            say(b.dataset.msg, info && info.setup_ssid
              ? 'Restarting into setup mode. Join the ' + info.setup_ssid + ' Wi-Fi and open http://192.168.4.1.'
              : 'Restarting into setup mode. Join the Juggle-SETUP Wi-Fi and open http://192.168.4.1 ' +
                '(on Ethernet: https://' + (info ? info.name : '<name>') + '.local/), in about a minute.', true);
          }).catch(function (e) { say(b.dataset.msg, 'Not done: ' + e.message); });
        });
      });
    });
    document.querySelectorAll('[data-reset]').forEach(function (b) {
      b.addEventListener('click', function () {
        if (!confirm(b.dataset.confirm)) return;
        var msg = el(b.dataset.msg);
        api('/api/reset', { confirm: true }).then(function () { say(b.dataset.msg, 'Reset. Restarting…', true); waitForRestart(msg); })
          .catch(function (e) { say(b.dataset.msg, 'Not reset: ' + e.message); });
      });
    });
  }

  // The header (name, state badge, health chips, problems) and footer, from /api/info every 3 s

  function chip(text, cls) { return mk('span', { className: 'chip' + (cls ? ' ' + cls : ''), text: text }); }

  function render(i) {
    info = i;
    document.title = i.name;
    el('name').textContent = i.name;
    var up = i.app_uptime_s != null ? i.app_uptime_s : i.uptime_s, foot = [i.name, i.product, 'v' + i.version];
    if (i.time) foot.push(i.time.slice(11, 16));
    foot.push('up ' + duration(up));
    if (i.mem_free != null) foot.push(i.mem_free > 4194304 ? Math.round(i.mem_free / 1048576) + ' MB free'
      : Math.round(i.mem_free / 1024) + ' KB free');
    if (i.os && i.os.version) foot.push('Juggle OS ' + i.os.version + (i.os.slot ? ' (slot ' + i.os.slot + ')' : ''));
    el('footer').textContent = foot.join(' • ');

    var health = el('health'), problems = [];
    health.textContent = '';
    if (i.temperature_c != null) health.appendChild(chip(i.temperature_c + ' °C', i.temperature_c >= 80 ? 'bad' : ''));
    if (i.mqtt) {
      if (!i.mqtt.enabled) health.appendChild(chip('MQTT off'));
      else if (i.mqtt.connected) health.appendChild(chip('MQTT connected', 'ok'));
      else {
        health.appendChild(chip('MQTT not connected', 'bad'));
        problems.push('MQTT is not connected' + (i.mqtt.error ? ': ' + i.mqtt.error : '.'));
      }
    }
    // What the OS does for a Juggle app: shown where it takes the app's requests
    var requests = !!(i.os && i.os.requests);
    document.querySelectorAll('[data-os-requests]').forEach(function (n) { n.hidden = !requests; });
    // Setup mode: the OS's on a Pi (i.os.setup), the device's own on a Pico (i.setup)
    var setup = !!(i.setup || (i.os && i.os.setup));
    document.querySelectorAll('[data-os-setup]').forEach(function (n) { n.hidden = !setup; });
    // A name the device changes itself (a Pico's), rather than the OS's
    document.querySelectorAll('[data-name-editable]').forEach(function (n) { n.hidden = !i.name_editable; });
    if (el('update_note')) {
      var u = i.update;
      el('update_note').textContent = !u ? '' : (u.success ? '' : 'The last update failed: ') + u.message +
        (u.time ? ' (' + new Date(u.time * 1000).toLocaleString() + ')' : '');
      el('update_note').className = 'note' + (u && !u.success ? ' bad' : '');
    }
    showAlert(problems.join(' '));
    infoListeners.forEach(function (f) { f(i); });
  }

  function refresh() {
    if (restarting) return Promise.resolve();
    return api('/api/info').then(render).catch(function () {
      showAlert("Can't reach the device. It may be restarting; this page keeps trying.");
    });
  }

  // A light's controls, as PlasmaStick's: the power switch, brightness, the mode buttons (from the light's modes), and
  // the speed, colour and white rows. opts.speed: the modes with a speed; opts.colour: the modes the colour picker
  // sets (Colour's own, or another's); opts.acted(): after each change is sent.
  function light(opts) {
    var expected = {}, modeButtons = [], defaultColour = null;
    var sending = { colour: false, brightness: false }, pending = { colour: null, brightness: null };
    opts = opts || {};
    var speedModes = opts.speed || [], colourModes = opts.colour || ['Colour'];

    function expect(key, value) { expected[key] = { value: value, until: Date.now() + 3000 }; }
    // The device's value, or what was just asked for if it hasn't caught up yet
    function shown(l, key) {
      var e = expected[key];
      if (e) {
        if (l[key] === e.value || Date.now() > e.until) delete expected[key];
        else return e.value;
      }
      return l[key];
    }
    function act(path, body) {
      say('msg', '');
      return api(path, body).catch(function (e) { say('msg', e.message); })
        .then(function () { if (opts.acted) opts.acted(); setTimeout(refresh, 300); });
    }

    function buildModes(modes) {
      modes.forEach(function (m) {
        var b = mk('button', { type: 'button', value: m, text: m, 'aria-pressed': 'false' });
        b.addEventListener('click', function () {
          expect('mode', m);
          showMode(m);
          act('/api/mode', m === 'Colour' ? { mode: m, parameters: { Colour: el('colour').value } } : { mode: m });
        });
        el('modes').appendChild(b);
        modeButtons.push(b);
        if (el('default_mode') && !el('default_mode').querySelector('option[value="' + m + '"]')) {
          el('default_mode').appendChild(mk('option', { value: m, text: m }));
        }
      });
      if (config) fill(config);  // the Default Mode list has its options now
    }

    function showMode(mode) {
      var l = info && info.light;
      modeButtons.forEach(function (b) { b.setAttribute('aria-pressed', b.value === mode ? 'true' : 'false'); });
      el('speed_row').hidden = speedModes.indexOf(mode) < 0;
      if (l && document.activeElement !== el('speed')) {
        var speed = (mode === l.mode && l.parameters.Speed) || (config && config.device.effect_speed) || 5;
        el('speed').value = speed;
        el('speed_text').textContent = speed;
      }
      var colourOn = colourModes.indexOf(mode) >= 0;
      el('colour').disabled = !colourOn;
      el('kelvin').disabled = mode !== 'Colour';
      var kelvin = l && l.mode === 'Colour' && l.parameters.Kelvin;
      if (document.activeElement !== el('kelvin')) {
        if (kelvin) el('kelvin').value = kelvin;
        // A colour picked any other way isn't a white temperature: no figure for it
        el('kelvin_text').textContent = kelvin ? kelvin + ' K' : '—';
      }
      el('colour_text').textContent = mode === 'Colour' ? 'Pick a colour' : colourOn ? mode + ' colour' : 'Colour mode only';
      showDefault(mode);
    }

    // Default and Set as Default: Colour mode only, and not when the default is already showing
    function showDefault(mode) {
      if (!defaultColour) return;
      el('default_swatch').style.backgroundColor = defaultColour;
      var l = info && info.light, current = l && l.mode === 'Colour' && l.parameters.Colour;
      el('default_btn').disabled = el('set_default_btn').disabled = mode !== 'Colour' ||
        (!!current && !sending.colour && current.toLowerCase() === defaultColour.toLowerCase());
    }

    function update(i) {
      var l = i.light;
      if (!modeButtons.length) buildModes(l.modes);
      if (config) defaultColour = config.device.default_colour;
      var on = shown(l, 'state'), mode = shown(l, 'mode');
      el('state').textContent = l.alert && l.alert.active ? 'Alert' : on ? 'On' : 'Off';
      el('state').className = 'badge' + (on || (l.alert && l.alert.active) ? ' on' : '');
      el('power').setAttribute('aria-checked', on ? 'true' : 'false');
      if (!sending.brightness) {
        var b = shown(l, 'brightness');
        el('brightness').value = b;
        el('brightness_text').textContent = b + '%';
      }
      // Don't overwrite a colour while it's being picked
      if (!sending.colour && document.activeElement !== el('colour') && colourModes.indexOf(l.mode) >= 0) {
        el('colour').value = (l.parameters.Colour || (opts.colours || {})[l.mode] || '#ffb46b').toLowerCase();
      }
      showMode(mode);
    }
    infoListeners.push(update);

    el('power').addEventListener('click', function () {
      var on = !(info && info.light.state);
      el('power').setAttribute('aria-checked', on ? 'true' : 'false');  // at once
      expect('state', on);
      act('/api/power/' + (on ? 'on' : 'off'), {});
    });

    // Sliders and the colour picker apply as they move, one request at a time; a change made meanwhile is sent next
    function sender(key, send) {
      function go() {
        sending[key] = true;
        var value = pending[key];
        pending[key] = null;
        send(value).catch(function (e) { say('msg', e.message); }).then(function () {
          if (pending[key] !== null) { setTimeout(go, 100); return; }
          sending[key] = false;
          if (opts.acted) opts.acted();
          setTimeout(refresh, 300);
        });
      }
      return function (value) {
        say('msg', '');
        pending[key] = value;
        if (!sending[key]) go();
      };
    }
    var sendBrightness = sender('brightness', function (b) {
      expect('brightness', b);
      return api('/api/brightness', { brightness: b });
    });
    el('brightness').addEventListener('input', function () {
      el('brightness_text').textContent = el('brightness').value + '%';
      sendBrightness(Math.round(Number(el('brightness').value)));
    });
    var sendColour = sender('colour', function (c) {
      var mode = info && colourModes.indexOf(info.light.mode) >= 0 ? info.light.mode : 'Colour';
      return api('/api/mode', { mode: mode, parameters: { Colour: c } });
    });
    el('colour').addEventListener('input', function () { sendColour(el('colour').value); });

    el('default_btn').addEventListener('click', function () {
      if (defaultColour) el('colour').value = defaultColour.toLowerCase();
      el('default_btn').disabled = true;
      expect('mode', 'Colour');
      act('/api/mode', { mode: 'Colour' });  // no colour: the default
    });
    // The colour showing becomes the default
    el('set_default_btn').addEventListener('click', function () {
      var l = info && info.light, c = l && l.parameters.Colour;
      if (!c) return;
      el('set_default_btn').disabled = true;
      api('/api/config', { device: { default_colour: c.toLowerCase() } }).then(function () {
        defaultColour = c;
        say('msg', 'Saved as the default colour.', true);
        setTimeout(function () { say('msg', ''); }, 3000);
        loadSettings();
      }).catch(function (e) { say('msg', 'Not saved: ' + e.message); })
        .then(function () { showDefault(info ? info.light.mode : null); });
    });

    // White and speed: sent when the slider is let go
    el('kelvin').addEventListener('input', function () { el('kelvin_text').textContent = el('kelvin').value + ' K'; });
    el('kelvin').addEventListener('change', function () {
      expect('mode', 'Colour');
      act('/api/mode', { mode: 'Colour', parameters: { Kelvin: Number(el('kelvin').value) } });
    });
    el('speed').addEventListener('input', function () { el('speed_text').textContent = el('speed').value; });
    el('speed').addEventListener('change', function () {
      var mode = info && shown(info.light, 'mode');
      if (mode) act('/api/mode', { mode: mode, parameters: { Speed: Number(el('speed').value) } });
    });

    return { act: act, expect: expect };
  }

  // The schedule, as PlasmaStick's: the events (schedule.events, up to 8), the location for sunrise and sunset, and
  // the clock, which on a Pi is the Pi's own. labels: what on and off are called (a light's: Turn on, Turn off); a
  // device that isn't a light has no mode or brightness in its events
  function schedule(labels) {
    labels = labels || ['Turn on', 'Turn off'];
    var DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    var form = el('events').closest('form');

    function options(select, list, value) {
      list.forEach(function (o) { select.appendChild(mk('option', { value: o[0], text: o[1] })); });
      select.value = value;
      return select;
    }
    function eventRow(ev) {
      var days = mk('div', { className: 'days', 'aria-label': 'Days' }, DAY_NAMES.map(function (d, n) {
        return mk('label', {}, [mk('input', { type: 'checkbox', checked: ev.days.indexOf(n) >= 0, 'aria-label': d }),
          document.createTextNode(d.charAt(0))]);
      }));
      var sun = ev.at === 'sunrise' || ev.at === 'sunset';
      var when = options(mk('select', { 'aria-label': 'When', 'data-k': 'when' }),
        [['time', 'At'], ['sunrise', 'Sunrise'], ['sunset', 'Sunset']], sun ? ev.at : 'time');
      var time = mk('input', { type: 'time', value: sun ? '18:00' : ev.at, 'aria-label': 'Time', 'data-k': 'time' });
      var offset = mk('input', { type: 'number', min: -180, max: 180, value: ev.offset || 0,
        'aria-label': 'Minutes after (negative: before)', 'data-k': 'offset' });
      var offsetLabel = mk('span', { className: 'note', text: 'min' });
      function showWhen() {
        var isSun = when.value !== 'time';
        time.hidden = isSun;
        offset.hidden = offsetLabel.hidden = !isSun;
      }
      when.addEventListener('change', showWhen);
      var action = options(mk('select', { 'aria-label': 'Action', 'data-k': 'action' }),
        [['on', labels[0]], ['off', labels[1]]], ev.action);
      var lightless = !(info && info.light);
      var modes = [['', 'Same mode']].concat((lightless ? [] : info.light.modes).map(function (m) { return [m, m]; }));
      var mode = options(mk('select', { 'aria-label': 'Mode', 'data-k': 'mode' }), modes, ev.mode || '');
      var bright = mk('input', { type: 'number', min: 1, max: 100, placeholder: '%', value: ev.brightness || '',
        'aria-label': 'Brightness (blank: unchanged)', 'data-k': 'brightness' });
      function showAction() { mode.hidden = bright.hidden = lightless || action.value === 'off'; }
      action.addEventListener('change', showAction);
      var row = mk('div', { className: 'event' }, [days, when, time, offset, offsetLabel, action, mode, bright]);
      row.appendChild(mk('button', { type: 'button', className: 'reset remove', text: 'Remove', onclick: function () {
        row.remove();
        el('add_event').disabled = false;
      } }));
      showWhen();
      showAction();
      return row;
    }
    function showEvents(events) {
      el('events').textContent = '';
      events.forEach(function (ev) { el('events').appendChild(eventRow(ev)); });
      el('add_event').disabled = events.length >= 8;
    }
    function readEvents() {
      return Array.prototype.map.call(el('events').children, function (row) {
        function f(k) { return row.querySelector('[data-k=' + k + ']'); }
        var ev = { days: [], action: f('action').value };
        row.querySelectorAll('.days input').forEach(function (c, n) { if (c.checked) ev.days.push(n); });
        if (f('when').value === 'time') ev.at = f('time').value;
        else { ev.at = f('when').value; ev.offset = Number(f('offset').value) || 0; }
        if (ev.action === 'on') {
          if (f('mode').value) ev.mode = f('mode').value;
          if (f('brightness').value) ev.brightness = Number(f('brightness').value);
        }
        return ev;
      });
    }
    // The events are saved with the form, whatever else changed
    form.extra = function (updates) { (updates.schedule = updates.schedule || {}).events = readEvents(); };
    el('add_event').addEventListener('click', function () {
      el('events').appendChild(eventRow({ days: [0, 1, 2, 3, 4, 5, 6], at: '18:00', action: 'on' }));
      el('add_event').disabled = el('events').children.length >= 8;
    });

    // Today's sunrise and sunset at the location entered (the same sums as the device's), to check it before saving
    function sunTimes(lat, lon, date) {
      var rad = Math.PI / 180, days = Math.floor(date.getTime() / 86400000);
      var n = days - 10957 - lon / 360, m = (357.5291 + 0.98560028 * n) % 360;
      var c = 1.9148 * Math.sin(m * rad) + 0.02 * Math.sin(2 * m * rad) + 0.0003 * Math.sin(3 * m * rad);
      var ecl = (m + c + 180 + 102.9372) % 360;
      var transit = 2451545 + n + 0.0053 * Math.sin(m * rad) - 0.0069 * Math.sin(2 * ecl * rad);
      var dec = Math.asin(Math.sin(ecl * rad) * Math.sin(23.4397 * rad));
      var ch = (Math.sin(-0.833 * rad) - Math.sin(lat * rad) * Math.sin(dec)) / (Math.cos(lat * rad) * Math.cos(dec));
      if (ch < -1 || ch > 1) return null;
      var h = Math.acos(ch) / rad / 360;
      return [transit - h, transit + h].map(function (jd) { return new Date((jd - 2440587.5) * 86400000); });
    }
    function showSun() {
      var lat = parseFloat(el('latitude').value), lon = parseFloat(el('longitude').value);
      var base = 'For sunrise and sunset events. East and north are positive.';
      if (isNaN(lat) || isNaN(lon)) { el('sun_note').textContent = base; return; }
      var t = sunTimes(lat, lon, new Date()), hm = function (d) { return d.toTimeString().slice(0, 5); };
      el('sun_note').textContent = base + (t ? ' Today here: sunrise ' + hm(t[0]) + ', sunset ' + hm(t[1]) +
        ' (this browser’s time).' : ' The sun doesn’t rise or set there today.');
    }
    el('latitude').addEventListener('input', showSun);
    el('longitude').addEventListener('input', showSun);

    // The time zone and time server, which are the OS's (a Juggle app asks it to change them)
    var clockLoaded = false;
    function loadClock() {
      clockLoaded = true;
      api('/api/clock').then(function (c) {
        var select = el('time_zone');
        select.textContent = '';
        c.zones.forEach(function (z) { select.appendChild(mk('option', { value: z, text: z.replace(/_/g, ' ') })); });
        select.value = c.time_zone || '';
        el('ntp').value = c.ntp || '';
      }).catch(function (e) { say('clock_msg', 'Could not load the clock settings: ' + e.message); });
    }
    if (el('clock_save')) {
      el('clock_save').addEventListener('click', function () {
        say('clock_msg', 'Saving…', true);
        api('/api/clock', { time_zone: el('time_zone').value, ntp: el('ntp').value.trim() }).then(function () {
          say('clock_msg', 'Saved.', true);
          refresh();
        }).catch(function (e) { say('clock_msg', 'Not saved: ' + e.message); });
      });
    }

    var shownConfig = null;
    infoListeners.push(function (i) {
      if (el('clock_save') && !clockLoaded && i.os && i.os.requests) loadClock();
      if (el('clock_note')) {
        el('clock_note').textContent = i.time
          ? 'The time here is ' + i.time.slice(11, 16) + (i.time_zone ? ' (' + i.time_zone + ')' : '') + '.'
          : 'The clock isn’t set yet (it comes from the internet), so the schedule waits for it.';
      }
      // The events need the modes (from /api/info) and the settings
      if (config && config !== shownConfig) {
        shownConfig = config;
        showEvents(config.schedule.events || []);
        showSun();
      }
    });
  }

  // The Wi-Fi fallback (the OS's): setup mode by itself when Wi-Fi doesn't connect, after and for so many minutes.
  // Its fields are wifi_fallback_enabled, wifi_fallback_after and wifi_fallback_for, saved by wifi_save.
  function wifi() {
    if (!el('wifi_save')) return;
    var loaded = false;
    infoListeners.push(function (i) {
      if (loaded || !(i.setup || (i.os && i.os.setup))) return;
      loaded = true;
      api('/api/wifi').then(function (w) {
        el('wifi_fallback_enabled').checked = w.fallback.enabled;
        el('wifi_fallback_after').value = w.fallback.after_minutes;
        el('wifi_fallback_for').value = w.fallback.for_minutes;
        // Changing the network here, where the device can (a Pico): otherwise it's setup mode's
        document.querySelectorAll('[data-wifi-change]').forEach(function (n) { n.hidden = !w.change; });
        document.querySelectorAll('[data-wifi-setup]').forEach(function (n) { n.hidden = !!w.change; });
        if (w.change && el('wifi_current')) el('wifi_current').textContent = w.ssid || 'none';
      }).catch(function (e) { say('wifi_msg', 'Could not load the Wi-Fi settings: ' + e.message); });
    });
    // The network: chosen from a scan (wifi_scan fills wifi_network) or typed (wifi_network_other, for a hidden one),
    // with wifi_password, and changed by wifi_change: the device restarts onto it, and goes back to the network it
    // had if the new one doesn't connect within a few minutes
    if (el('wifi_change')) {
      el('wifi_scan').addEventListener('click', function () {
        say('wifi_change_msg', 'Looking for networks…', true);
        api('/api/wifi/networks').then(function (r) {
          var select = el('wifi_network');
          select.textContent = '';
          r.networks.forEach(function (n) { select.appendChild(mk('option', { value: n, text: n })); });
          say('wifi_change_msg', r.networks.length + ' network(s) found.', true);
        }).catch(function (e) { say('wifi_change_msg', 'Could not scan: ' + e.message); });
      });
      el('wifi_change').addEventListener('click', function () {
        var ssid = el('wifi_network_other').value.trim() || el('wifi_network').value;
        if (!ssid) { say('wifi_change_msg', 'Choose a network (Scan), or type its name.'); return; }
        if (!confirm('Change to ' + ssid + '? The device restarts onto it. If it can’t connect within 5 minutes, ' +
          'it goes back to the network it has now.')) return;
        say('wifi_change_msg', 'Saving…', true);
        api('/api/wifi', { network: { ssid: ssid, password: el('wifi_password').value } }).then(function () {
          el('wifi_password').value = '';
          say('wifi_change_msg', 'Saved: restarting onto ' + ssid + '. If it’s on another network, this page won’t ' +
            'come back here; if it can’t connect, the device goes back to the network it had.', true);
          waitForRestart(el('wifi_change_msg'));
        }).catch(function (e) { say('wifi_change_msg', 'Not changed: ' + e.message); });
      });
    }
    el('wifi_save').addEventListener('click', function () {
      say('wifi_msg', 'Saving…', true);
      api('/api/wifi', { fallback: {
        enabled: el('wifi_fallback_enabled').checked,
        after_minutes: parseInt(el('wifi_fallback_after').value, 10),
        for_minutes: parseInt(el('wifi_fallback_for').value, 10)
      } }).then(function () { say('wifi_msg', 'Saved: from the next start.', true); })
        .catch(function (e) { say('wifi_msg', 'Not saved: ' + e.message); });
    });
  }

  // Renaming a device whose name is its own (a Pico's): the field device_name, saved by rename_save (POST /api/name),
  // from the next start
  function rename() {
    if (!el('rename_save')) return;
    var filled = false;
    infoListeners.push(function (i) {
      if (filled || !i.name_editable) return;
      filled = true;
      el('device_name').value = i.name;
    });
    el('rename_save').addEventListener('click', function () {
      say('rename_msg', 'Saving…', true);
      api('/api/name', { name: el('device_name').value.trim() }).then(function () {
        say('rename_msg', 'Saved: restart for it to be used (the page is then at the new name).', true);
      }).catch(function (e) { say('rename_msg', 'Not saved: ' + e.message); });
    });
  }

  function start() {
    tabs();
    settingsForms();
    actions();
    wifi();
    rename();
    setInterval(function () { if (!document.hidden) refresh(); }, 3000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
    // The settings load after the first /api/info, which brings the modes the Default Mode list needs
    return refresh().then(loadSettings).then(function () { if (info) render(info); });
  }

  return {
    el: el, api: api, mk: mk, duration: duration, say: say, start: start, refresh: refresh,
    loadSettings: loadSettings, light: light, schedule: schedule,
    info: function () { return info; }, config: function () { return config; },
    onInfo: function (f) { infoListeners.push(f); }
  };
})();
