from log import Log

TAG = "WEBUI"

class WebUI:
    def __init__(self, api):
        self.api = api
        self._html_template = self._build_template()

    def _build_template(self):
        """Build HTML template once at startup with placeholders for variables."""
        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{device_name}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:#667eea;min-height:100vh;padding:16px;display:flex;justify-content:center;align-items:flex-start}}
.card{{background:#fff;border-radius:12px;width:100%;max-width:500px;overflow:hidden}}
header{{padding:20px 20px 0;text-align:center}}
h1{{font-size:21px;color:#333;margin-bottom:6px}}
.badge{{display:inline-block;padding:3px 12px;border-radius:10px;font-size:12px;font-weight:700;margin-bottom:2px}}
.on{{background:#4caf50;color:#fff}}
.off{{background:#f44336;color:#fff}}
/* CSS-only tabs - radio inputs drive panel visibility */
input[name=t]{{display:none}}
nav{{display:flex;border-bottom:2px solid #eee}}
nav label{{flex:1;padding:11px 4px;text-align:center;cursor:pointer;font-size:13px;font-weight:600;color:#aaa;border-bottom:3px solid transparent;transition:.15s}}
nav label:hover{{color:#555}}
.pane{{display:none;padding:20px}}
#tc:checked~nav label[for=tc],
#td:checked~nav label[for=td],
#tn:checked~nav label[for=tn]{{color:#667eea;border-bottom-color:#667eea}}
#tc:checked~#pc,#td:checked~#pd,#tn:checked~#pn{{display:block}}
/* CSS-only mode tabs */
input[name=m]{{display:none}}
.mtabs{{display:flex;gap:6px;margin-bottom:12px}}
.mtabs label{{flex:1;padding:8px;text-align:center;border:1.5px solid #ddd;border-radius:7px;cursor:pointer;font-size:12px;font-weight:700;color:#888;transition:.15s}}
.mpane{{display:none}}
#mc:checked~.mtabs label[for=mc],
#mr:checked~.mtabs label[for=mr],
#mh:checked~.mtabs label[for=mh]{{border-color:#667eea;background:#667eea;color:#fff}}
#mc:checked~#mpc,#mr:checked~#mpr,#mh:checked~#mph{{display:block}}
/* Sections */
.sec{{font-size:11px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;margin:16px 0 8px}}
.sec:first-child{{margin-top:0}}
/* Power row - inline forms */
.prow{{display:flex;gap:8px;margin-bottom:4px}}
.prow form{{flex:1}}
/* Generic inputs */
.frow{{display:flex;gap:8px;margin-bottom:8px}}
.field{{flex:1;display:flex;flex-direction:column;gap:3px}}
.field label{{font-size:12px;color:#555;font-weight:500}}
input[type=text],input[type=number],input[type=password],input[type=url],select{{width:100%;padding:8px 10px;border:1.5px solid #ddd;border-radius:6px;font-size:13px}}
input:focus,select:focus{{outline:none;border-color:#667eea}}
input[type=color]{{width:44px;height:36px;padding:2px;border:1.5px solid #ddd;border-radius:6px;cursor:pointer}}
.crow{{display:flex;gap:8px;align-items:center;margin-bottom:8px}}
.crow input[type=text]{{flex:1}}
/* Buttons */
.btn{{width:100%;padding:10px;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer}}
.grn{{background:#4caf50;color:#fff}}
.red{{background:#f44336;color:#fff}}
.blu{{background:#667eea;color:#fff}}
.btn-full{{display:block;width:100%;margin-top:10px;padding:11px;border:none;border-radius:7px;font-size:13px;font-weight:700;cursor:pointer;background:#667eea;color:#fff}}
footer{{font-size:11px;color:#bbb;text-align:center;padding:10px 0 14px}}
</style>
</head>
<body>
<div class="card">
<header>
  <h1>{device_name}</h1>
  <span class="badge {state_cls}">Status: {state_text}</span>
</header>

<input type="radio" name="t" id="tc" checked>
<input type="radio" name="t" id="td">
<input type="radio" name="t" id="tn">
<nav>
  <label for="tc">Control</label>
  <label for="td">Device</label>
  <label for="tn">Network</label>
</nav>

<!-- CONTROL PANEL -->
<div class="pane" id="pc">
  <div class="sec">Power</div>
  <div class="prow">
    <form action="/api/power/on" method="post"><button class="btn grn">ON</button></form>
    <form action="/api/power/off" method="post"><button class="btn red">OFF</button></form>
    <form action="/api/power/toggle" method="post"><button class="btn blu">Toggle</button></form>
  </div>

  <div class="sec">Mode</div>
  <input type="radio" name="m" id="mc" {mc}>
  <input type="radio" name="m" id="mr" {mr}>
  <input type="radio" name="m" id="mh" {mh}>
  <div class="mtabs">
    <label for="mc">Colour</label>
    <label for="mr">Rainbows</label>
    <label for="mh">CheerLights</label>
  </div>
  <div class="mpane" id="mpc">
    <form action="/api/mode" method="post">
      <input type="hidden" name="mode" value="Colour">
      <div class="crow">
        <input type="color" name="colour" value="{default_colour}">
        <button class="btn blu" style="flex:1">Apply Colour</button>
      </div>
    </form>
  </div>
  <div class="mpane" id="mpr">
    <form action="/api/mode" method="post">
      <input type="hidden" name="mode" value="Rainbows">
      <button class="btn blu">Apply Rainbows</button>
    </form>
  </div>
  <div class="mpane" id="mph">
    <form action="/api/mode" method="post">
      <input type="hidden" name="mode" value="CheerLights">
      <button class="btn blu">Apply CheerLights</button>
    </form>
  </div>
</div>

<!-- DEVICE PANEL -->
<div class="pane" id="pd">
  <form action="/ctrl/config" method="post">
    <input type="hidden" name="tab" value="device">
    <div class="sec">Identity</div>
    <div class="frow">
      <div class="field"><label>Device Name</label>
        <input type="text" name="device_name" value="{device_name_val}" maxlength="32">
      </div>
    </div>
    <div class="sec">LED Hardware</div>
    <div class="frow">
      <div class="field"><label>LED Count</label>
        <input type="number" name="device_led_count" value="{device_led_count}" min="1" max="300">
      </div>
      <div class="field"><label>Type</label>
        <select name="device_led_rgbw">
          <option value="false" {led_rgb_sel}>RGB</option>
          <option value="true" {led_rgbw_sel}>RGBW</option>
        </select>
      </div>
    </div>
    <div class="sec">Defaults</div>
    <div class="frow">
      <div class="field"><label>Default State</label>
        <select name="default_state">
          <option value="false" {default_state_off}>OFF</option>
          <option value="true" {default_state_on}>ON</option>
        </select>
      </div>
      <div class="field"><label>Default Mode</label>
        <select name="default_mode">
          <option value="Colour" {default_mode_colour}>Colour</option>
          <option value="Rainbows" {default_mode_rainbows}>Rainbows</option>
          <option value="CheerLights" {default_mode_cheerlights}>CheerLights</option>
        </select>
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Default Colour</label>
        <div class="crow">
          <input type="color" name="default_colour" value="{default_colour}">
          <span style="font-size:12px;color:#888">{default_colour}</span>
        </div>
      </div>
    </div>
    <div class="sec">CheerLights</div>
    <div class="frow">
      <div class="field"><label>API URL</label>
        <input type="url" name="cheerlights_url" value="{cheerlights_url}">
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Update Interval (s)</label>
        <input type="number" name="cheerlights_update_interval" value="{cheerlights_update_interval}" min="10">
      </div>
    </div>
    <button type="submit" class="btn-full">Save &amp; Restart</button>
  </form>
</div>

<!-- NETWORK PANEL -->
<div class="pane" id="pn">
  <form action="/ctrl/config" method="post">
    <input type="hidden" name="tab" value="network">
    <div class="sec">WiFi</div>
    <div class="frow">
      <div class="field"><label>SSID (from provisioning)</label>
        <input type="text" value="{ssid}" disabled>
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Connect Timeout (s)</label>
        <input type="number" name="connect_timeout" value="{connect_timeout}" min="5">
      </div>
      <div class="field"><label>Reconnect Wait (s)</label>
        <input type="number" name="reconnect_wait" value="{reconnect_wait}" min="5">
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Max Retries (0=unlimited)</label>
        <input type="number" name="max_retries" value="{max_retries}" min="0">
      </div>
    </div>
    <div class="sec">MQTT</div>
    <div class="frow">
      <div class="field"><label>Enabled</label>
        <select name="mqtt_enabled">
          <option value="false" {mqtt_disabled}>No</option>
          <option value="true" {mqtt_enabled}>Yes</option>
        </select>
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Broker Address</label>
        <input type="text" name="broker_address" value="{broker_address}">
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Username</label>
        <input type="text" name="mqtt_user" value="{mqtt_user}">
      </div>
      <div class="field"><label>Password</label>
        <input type="password" name="mqtt_password" placeholder="leave blank to keep">
      </div>
    </div>
    <div class="frow">
      <div class="field"><label>Queue Length</label>
        <input type="number" name="queue_len" value="{queue_len}" min="1">
      </div>
      <div class="field"><label>Clean Session</label>
        <select name="clean">
          <option value="true" {clean_yes}>Yes</option>
          <option value="false" {clean_no}>No</option>
        </select>
      </div>
    </div>
    <button type="submit" class="btn-full">Save &amp; Restart</button>
  </form>
</div>

<footer>{device_name} &bull; PlasmaStick</footer>
</div>
</body>
</html>"""

    def get_html(self):
        """Generate HTML by substituting variables into cached template."""
        dc = self.api.device_config
        state = self.api.led_strip.current_state
        state_text = 'ON' if state else 'OFF'
        state_cls = 'on' if state else 'off'
        mode = self.api.led_strip.current_mode
        default_colour = (dc.mode_parameters or {}).get('Colour', '#ffffff')
        
        # Build attribute strings
        mc = 'checked' if mode == 'Colour' else ''
        mr = 'checked' if mode == 'Rainbows' else ''
        mh = 'checked' if mode == 'CheerLights' else ''
        led_rgb_sel = 'selected' if not dc.device_led_rgbw else ''
        led_rgbw_sel = 'selected' if dc.device_led_rgbw else ''
        default_state_off = 'selected' if not dc.default_state else ''
        default_state_on = 'selected' if dc.default_state else ''
        default_mode_colour = 'selected' if dc.default_mode == 'Colour' else ''
        default_mode_rainbows = 'selected' if dc.default_mode == 'Rainbows' else ''
        default_mode_cheerlights = 'selected' if dc.default_mode == 'CheerLights' else ''
        mqtt_enabled = 'selected' if dc.mqttconfig.enabled else ''
        mqtt_disabled = 'selected' if not dc.mqttconfig.enabled else ''
        clean_yes = 'selected' if dc.mqttconfig.clean else ''
        clean_no = 'selected' if not dc.mqttconfig.clean else ''
        
        ssid = dc.wlanconfig.ssid if dc.wlanconfig else ''
        connect_timeout = dc.wlanconfig.connect_timeout if dc.wlanconfig else 30
        reconnect_wait = dc.wlanconfig.reconnect_wait if dc.wlanconfig else 30
        max_retries = dc.wlanconfig.max_retries if dc.wlanconfig else 0
        broker_address = dc.mqttconfig.broker_address if dc.mqttconfig and dc.mqttconfig.broker_address else ''
        mqtt_user = dc.mqttconfig.user if dc.mqttconfig and dc.mqttconfig.user else ''
        queue_len = dc.mqttconfig.queue_len if dc.mqttconfig else 4
        
        return self._html_template.format(
            device_name=self.api.device_name,
            device_name_val=dc.device_name,
            state_text=state_text,
            state_cls=state_cls,
            mode=mode,
            mc=mc,
            mr=mr,
            mh=mh,
            default_colour=default_colour,
            device_led_count=dc.device_led_count,
            led_rgb_sel=led_rgb_sel,
            led_rgbw_sel=led_rgbw_sel,
            default_state_off=default_state_off,
            default_state_on=default_state_on,
            default_mode_colour=default_mode_colour,
            default_mode_rainbows=default_mode_rainbows,
            default_mode_cheerlights=default_mode_cheerlights,
            cheerlights_url=dc.cheerlights_url,
            cheerlights_update_interval=dc.cheerlights_update_interval,
            ssid=ssid,
            connect_timeout=connect_timeout,
            reconnect_wait=reconnect_wait,
            max_retries=max_retries,
            mqtt_enabled=mqtt_enabled,
            mqtt_disabled=mqtt_disabled,
            broker_address=broker_address,
            mqtt_user=mqtt_user,
            queue_len=queue_len,
            clean_yes=clean_yes,
            clean_no=clean_no,
        )
